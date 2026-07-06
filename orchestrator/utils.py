"""Shared utilities for graph nodes."""

import contextlib
import getpass
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO, List, Optional

from orchestrator.paths import logs_base_dir, proxy_sessions_dir, workflow_logs_dir
from orchestrator.subprocess_registry import (
    SUBPROCESS_REGISTRY,
    get_current_workflow_id,
)


def _get_actor() -> str:
    """Return the current OS username, or 'unknown' if it cannot be determined."""
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _logs_dir() -> Path:
    return logs_base_dir()


def log_path(workflow_id: str, stage: str, ticket_key: Optional[str] = None) -> Path:
    """Return the log file path for a given workflow and stage (e.g. 'plan', 'execute')."""
    workflow_dir = workflow_logs_dir(workflow_id, ensure_dir=True)
    prefix = f"{ticket_key}_" if ticket_key else ""
    return workflow_dir / f"{prefix}{workflow_id}_{stage}.log"


# Azure deployment name → API version.
# Extend here when adding a new Azure deployment that requires a different api-version.
_AZURE_API_VERSIONS: dict[str, str] = {
    "gpt-4.1": "2024-12-01-preview",
    "gpt-5.3-codex": "2025-04-01-preview",
    "gpt-5.4": "preview",
    "Kimi-K2.6": "2024-12-01-preview",  # Azure Foundry model
}


def litellm_call_kwargs(model_string: str) -> dict:
    """Return kwargs for a direct litellm.completion() call for the given model string.

    Handles provider-specific translation that mirrors _litellm_config_yaml so that
    nodes calling litellm directly (without going through the proxy) use the same
    credentials and API base URLs as Goose sessions.
    """
    if "/" in model_string:
        provider, model_name = model_string.split("/", 1)
        provider = provider.lower()
    else:
        provider, model_name = "openai", model_string

    if provider == "azure":
        api_version = _AZURE_API_VERSIONS.get(model_name, os.environ.get("AZURE_API_VERSION", ""))
        return {
            "model": model_string,
            "api_key": os.environ.get("AZURE_API_KEY", ""),
            "api_base": os.environ.get("AZURE_API_BASE", ""),
            "api_version": api_version,
        }
    elif provider == "foundry":
        # Azure AI Foundry: translate to openai/ provider with the Foundry endpoint.
        return {
            "model": f"openai/{model_name}",
            "api_key": os.environ.get("AZURE_API_KEY", ""),
            "api_base": os.environ.get("AZURE_FOUNDRY_API_BASE", ""),
        }
    elif provider == "anthropic":
        return {
            "model": model_string,
            "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        }
    else:
        return {
            "model": model_string,
            "api_key": os.environ.get("OPENAI_API_KEY", ""),
        }


def _free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_proxy(
    port: int, timeout: float = 30.0, proc: Optional[subprocess.Popen] = None
) -> None:
    """Block until the litellm proxy on *port* responds to health check, or raise."""
    deadline = time.monotonic() + timeout
    # Try multiple health endpoints (different versions of LiteLLM use different paths)
    health_endpoints = [
        f"http://127.0.0.1:{port}/health",
        f"http://127.0.0.1:{port}/health/ready",
        f"http://127.0.0.1:{port}/health/liveliness",
    ]
    while time.monotonic() < deadline:
        # If the process died, fail fast with diagnostics.
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(
                f"litellm proxy process exited with code {proc.returncode}"
                f" before becoming healthy. Check the proxy log for details."
            )
        for url in health_endpoints:
            try:
                response = urllib.request.urlopen(url, timeout=5)
                if 200 <= response.status < 300:
                    # Success! Return immediately
                    return
            except urllib.error.HTTPError:
                # HTTPError means proxy is responding, but this endpoint returned non-2xx
                # Try next endpoint
                continue
            except Exception:
                # Connection refused or timeout, keep trying
                pass
        time.sleep(0.5)
    raise RuntimeError(f"litellm proxy on port {port} did not become healthy within {timeout}s")


def _litellm_config_yaml(model_string: str) -> str:
    """Generate a minimal litellm proxy config YAML for the given model string.

    The model_string is a litellm model identifier such as ``azure/gpt-5.3-codex``
    or ``anthropic/claude-3-5-sonnet-20241022``.  Provider credentials are
    referenced via ``os.environ/`` so no secrets are written to disk.
    """
    if "/" in model_string:
        provider, model_name = model_string.split("/", 1)
        provider = provider.lower()
    else:
        provider, model_name = "openai", model_string

    if provider == "azure":
        api_version = _AZURE_API_VERSIONS.get(model_name, os.environ.get("AZURE_API_VERSION", ""))
        litellm_params = (
            f"      model: {model_string}\n"
            f"      api_key: os.environ/AZURE_API_KEY\n"
            f"      api_base: os.environ/AZURE_API_BASE\n"
            f'      api_version: "{api_version}"\n'
        )
    elif provider == "foundry":
        # Azure AI Foundry models-as-a-service (non-OpenAI models like Kimi, Llama,
        # Mistral) exposed via the OpenAI-compatible /openai/v1 endpoint on the
        # Foundry resource. Routed through LiteLLM's openai provider with a custom
        # api_base. Shares AZURE_API_KEY with Azure OpenAI deployments.
        litellm_params = (
            f"      model: openai/{model_name}\n"
            f"      api_key: os.environ/AZURE_API_KEY\n"
            f"      api_base: os.environ/AZURE_FOUNDRY_API_BASE\n"
        )
    elif provider == "anthropic":
        litellm_params = (
            f"      model: {model_string}\n" f"      api_key: os.environ/ANTHROPIC_API_KEY\n"
        )
    else:
        litellm_params = (
            f"      model: {model_string}\n" f"      api_key: os.environ/OPENAI_API_KEY\n"
        )

    return (
        "model_list:\n"
        f"  - model_name: {model_string}\n"
        "    litellm_params:\n"
        f"{litellm_params}"
        "\n"
        "litellm_settings:\n"
        "  drop_params: true\n"
        # Routed through otel.litellm_proxy_setup so the proxy subprocess
        # installs the dispatcher's LocalJsonFileExporter and emits
        # ``llm.call`` spans into LOGS_DIR/<workflow_id>/otel.jsonl.
        #
        # The list form (vs scalar) is REQUIRED: LiteLLM's proxy YAML loader
        # replaces ``litellm.callbacks`` entirely when ``callbacks`` is a
        # scalar string (see ``initialize_callbacks_on_proxy``) which would
        # wipe out the ``OtelLiteLLMCallback`` that ``setup_tracing()``
        # appends during module import.  List form *extends* instead.
        "  callbacks:\n"
        "    - otel.litellm_proxy_setup.proxy_handler_instance\n"
    )


@contextlib.contextmanager
def goose_session(
    workflow_id: Optional[str] = None, stage: Optional[str] = None, ticket_key: Optional[str] = None
):
    """Start an ephemeral litellm proxy and yield a Goose env dict.

    Reads GOOSE_MODEL from the environment as a litellm model string
    (e.g. ``azure/gpt-5.3-codex``, ``anthropic/claude-3-5-sonnet-20241022``).
    A minimal proxy config is generated on the fly, the proxy is started on a
    random free port, and Goose is pointed at it via OPENAI_BASE_URL.

    The proxy subprocess is terminated when the context exits regardless of
    success or failure, and the temporary config file is removed.

    Usage::

        with goose_session() as env:
            run_and_tee(["goose", "run", ...], "subprocess.goose", env=env)
    """
    model = os.environ.get("GOOSE_MODEL", "")
    if not model:
        yield os.environ.copy()
        return

    port = _free_port()
    config_yaml = _litellm_config_yaml(model)

    import shutil as _shutil

    repo_root = Path(__file__).resolve().parents[1]
    # LiteLLM's ``get_instance_fn`` resolves callback modules by file path
    # relative to the config file's directory — it builds:
    #   <config_dir>/otel/litellm_proxy_setup.py
    # and requires that file to exist before falling back to Python import.
    # We create an isolated session dir under the XDG state location and
    # symlink ``otel/`` into it, so the file check passes while the config
    # lives outside the working tree.
    proxy_sessions_dir().mkdir(parents=True, exist_ok=True)
    config_dir = Path(
        tempfile.mkdtemp(prefix="goose_proxy_session_", dir=str(proxy_sessions_dir()))
    )
    (config_dir / "otel").symlink_to(repo_root / "otel")
    config_path = str(config_dir / "goose_proxy_litellm.yaml")
    proxy_log_fh: Optional[IO[str]] = None
    try:
        with open(config_path, "w") as f:
            f.write(config_yaml)

        # Resolve the litellm binary relative to the running Python interpreter
        # so it works regardless of whether the venv is activated in the shell.
        litellm_bin = Path(sys.executable).parent / "litellm"
        proxy_env = os.environ.copy()
        existing_pythonpath = proxy_env.get("PYTHONPATH", "")
        proxy_env["PYTHONPATH"] = (
            f"{repo_root}{os.pathsep}{existing_pythonpath}"
            if existing_pythonpath
            else str(repo_root)
        )
        if workflow_id:
            proxy_env["NGB_WORKFLOW_ID"] = workflow_id
        if stage:
            proxy_env["NGB_WORKFLOW_STAGE"] = stage
        # Forward ticket key so the proxy-side OtelContext can populate
        # ``jira.ticket_key`` on ``llm.call`` spans.
        if ticket_key:
            proxy_env["NGB_TICKET_KEY"] = ticket_key

        # Inject the active W3C traceparent so the proxy can parent every
        # ``llm.call`` span under the current dispatcher span (typically
        # ``graph.node.work_planner`` / ``graph.node.generate_code``). Without
        # this, each LiteLLM request lands in its own orphan trace because the
        # proxy subprocess has no shared OTel context with the dispatcher.
        try:
            from opentelemetry.propagate import inject as _otel_inject

            carrier: dict[str, str] = {}
            _otel_inject(carrier)
            if carrier.get("traceparent"):
                proxy_env["NGB_TRACEPARENT"] = carrier["traceparent"]
            if carrier.get("tracestate"):
                proxy_env["NGB_TRACESTATE"] = carrier["tracestate"]
        except Exception:
            # Best-effort — fall back to orphan llm.call traces.
            pass

        proxy_log = log_path(workflow_id or "proxy", "litellm_proxy", ticket_key=ticket_key)
        proxy_log_fh = open(proxy_log, "w")
        proc = subprocess.Popen(
            [str(litellm_bin), "--config", config_path, "--port", str(port)],
            stdout=proxy_log_fh,
            stderr=subprocess.STDOUT,
            env=proxy_env,
            start_new_session=True,
        )
        # Register with the workflow id from the calling thread (set by
        # BackgroundDispatcher._run) so cancel can terminate this process.
        _tracked_wf_id = get_current_workflow_id() or workflow_id
        if _tracked_wf_id:
            SUBPROCESS_REGISTRY.register(_tracked_wf_id, proc)
        try:
            try:
                _wait_for_proxy(port, proc=proc)
            except RuntimeError as exc:
                proxy_log_fh.flush()
                tail = ""
                try:
                    with open(proxy_log, "r") as _lf:
                        lines = _lf.readlines()
                        tail = "".join(lines[-80:])
                except Exception:
                    pass
                raise RuntimeError(
                    f"{exc}\n\nProxy log ({proxy_log}):\n{tail or '(empty)'}"
                ) from exc
            env = os.environ.copy()
            env["GOOSE_PROVIDER"] = "openai"
            env["GOOSE_MODEL"] = model
            env["OPENAI_BASE_URL"] = f"http://127.0.0.1:{port}"
            env["OPENAI_API_KEY"] = "sk-local"
            env["NGB_ORCHESTRATOR_ROOT"] = str(repo_root)
            yield env
        finally:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            finally:
                if _tracked_wf_id:
                    SUBPROCESS_REGISTRY.unregister(_tracked_wf_id, proc)
    finally:
        if proxy_log_fh is not None:
            proxy_log_fh.close()
        _shutil.rmtree(config_dir, ignore_errors=True)


def run_and_tee(
    cmd: List[str],
    logger_name: str = "subprocess",
    **kwargs,
) -> subprocess.CompletedProcess:
    """
    Run a command, streaming stdout+stderr through Python logging.

    Returns a CompletedProcess-like object with returncode set.
    All subprocess kwargs (cwd, env, etc.) are forwarded.

    When OTel tracing is active, emits a ``goose.run`` child span with:
      - ``process.command``       — first element of cmd (e.g. "goose")
      - ``process.command_line``  — full command joined as a string
      - ``process.exit_code``
      - ``goose.recipe``          — --recipe param value when present
      - ``goose.stage``           — recipe basename without extension (e.g. "plan")
      - ``goose.stdout_lines``    — number of stdout lines captured
      - correlation attributes from the current OTel context
    """
    is_goose = bool(cmd and cmd[0] == "goose")
    subprocess_logger = logging.getLogger(logger_name)

    def _goose_recipe(cmd: List[str]) -> str:
        """Extract --recipe value from a goose run command list."""
        try:
            idx = cmd.index("--recipe")
            return cmd[idx + 1]
        except (ValueError, IndexError):
            return ""

    if is_goose:
        try:
            from opentelemetry import trace as _trace
            from opentelemetry.trace import Status as _Status
            from opentelemetry.trace import StatusCode as _StatusCode

            from otel.context import OtelContext as _OtelContext

            tracer = _trace.get_tracer("graph.orchestrator")
            ctx = _OtelContext.capture()
            attributes: dict = {
                **ctx.as_attributes(),
                "process.command": cmd[0],
                "process.command_line": " ".join(cmd),
            }
            recipe = _goose_recipe(cmd)
            if recipe:
                attributes["goose.recipe"] = recipe
                # Derive a stable stage name (e.g. "recipes/plan.yaml" -> "plan").
                stage = os.path.splitext(os.path.basename(recipe))[0]
                if stage:
                    attributes["goose.stage"] = stage

            with tracer.start_as_current_span("goose.run", attributes=attributes) as span:
                kwargs.setdefault("stdout", subprocess.PIPE)
                kwargs.setdefault("stderr", subprocess.STDOUT)
                kwargs.setdefault("start_new_session", True)
                process = subprocess.Popen(cmd, **kwargs)
                _tracked_wf_id = get_current_workflow_id()
                if _tracked_wf_id:
                    SUBPROCESS_REGISTRY.register(_tracked_wf_id, process)
                try:
                    stdout_lines = 0
                    if process.stdout is not None:
                        for raw_line in process.stdout:
                            line = (
                                raw_line.decode(errors="replace")
                                if isinstance(raw_line, bytes)
                                else raw_line
                            )
                            subprocess_logger.info("%s", line.rstrip("\n"))
                            stdout_lines += 1
                    process.wait()
                finally:
                    if _tracked_wf_id:
                        SUBPROCESS_REGISTRY.unregister(_tracked_wf_id, process)
                span.set_attribute("process.exit_code", process.returncode)
                span.set_attribute("goose.stdout_lines", stdout_lines)
                if process.returncode != 0:
                    span.set_status(
                        _Status(_StatusCode.ERROR, f"goose exited with code {process.returncode}")
                    )
                else:
                    span.set_status(_Status(_StatusCode.OK))
                return subprocess.CompletedProcess(cmd, process.returncode)
        except ImportError:
            # OTel not installed — fall through to plain execution.
            pass

    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.STDOUT)
    kwargs.setdefault("start_new_session", True)

    process = subprocess.Popen(cmd, **kwargs)
    _tracked_wf_id = get_current_workflow_id()
    if _tracked_wf_id:
        SUBPROCESS_REGISTRY.register(_tracked_wf_id, process)
    try:
        assert process.stdout is not None

        for raw_line in process.stdout:
            line = raw_line.decode(errors="replace") if isinstance(raw_line, bytes) else raw_line
            subprocess_logger.info("%s", line.rstrip("\n"))

        process.wait()
    finally:
        if _tracked_wf_id:
            SUBPROCESS_REGISTRY.unregister(_tracked_wf_id, process)
    return subprocess.CompletedProcess(cmd, process.returncode)
