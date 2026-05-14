"""Shared utilities for graph nodes."""

import contextlib
import getpass
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import IO, List


def _get_actor() -> str:
    """Return the current OS username, or 'unknown' if it cannot be determined."""
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _logs_dir() -> Path:
    path = Path(os.getenv("LOGS_DIR", "logs"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path(workflow_id: str, stage: str) -> Path:
    """Return the log file path for a given workflow and stage (e.g. 'plan', 'execute')."""
    return _logs_dir() / f"{workflow_id}_{stage}.log"


# Azure deployment name → API version.
# Extend here when adding a new Azure deployment that requires a different api-version.
_AZURE_API_VERSIONS: dict[str, str] = {
    "gpt-4.1": "2024-12-01-preview",
    "gpt-5.3-codex": "2025-04-01-preview",
}


def _free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_proxy(port: int, timeout: float = 30.0) -> None:
    """Block until the litellm proxy on *port* responds to /health, or raise."""
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
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
    )


@contextlib.contextmanager
def goose_session():
    """Start an ephemeral litellm proxy and yield a Goose env dict.

    Reads GOOSE_MODEL from the environment as a litellm model string
    (e.g. ``azure/gpt-5.3-codex``, ``anthropic/claude-3-5-sonnet-20241022``).
    A minimal proxy config is generated on the fly, the proxy is started on a
    random free port, and Goose is pointed at it via OPENAI_BASE_URL.

    The proxy subprocess is terminated when the context exits regardless of
    success or failure, and the temporary config file is removed.

    Usage::

        with goose_session() as env:
            run_and_tee(["goose", "run", ...], log_file, env=env)
    """
    model = os.environ.get("GOOSE_MODEL", "")
    if not model:
        yield os.environ.copy()
        return

    port = _free_port()
    config_yaml = _litellm_config_yaml(model)

    config_fd, config_path = tempfile.mkstemp(suffix="_litellm.yaml", prefix="goose_proxy_")
    os.close(config_fd)
    try:
        with open(config_path, "w") as f:
            f.write(config_yaml)

        # Resolve the litellm binary relative to the running Python interpreter
        # so it works regardless of whether the venv is activated in the shell.
        litellm_bin = Path(sys.executable).parent / "litellm"
        proc = subprocess.Popen(
            [str(litellm_bin), "--config", config_path, "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_proxy(port)
            env = os.environ.copy()
            env["GOOSE_PROVIDER"] = "openai"
            env["GOOSE_MODEL"] = model
            env["OPENAI_BASE_URL"] = f"http://127.0.0.1:{port}"
            env["OPENAI_API_KEY"] = "sk-local"
            yield env
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    finally:
        try:
            os.unlink(config_path)
        except FileNotFoundError:
            pass


def run_and_tee(
    cmd: List[str],
    log_file: IO[str],
    **kwargs,
) -> subprocess.CompletedProcess:
    """
    Run a command, streaming stdout+stderr to both the terminal and log_file.

    Returns a CompletedProcess-like object with returncode set.
    All subprocess kwargs (cwd, env, etc.) are forwarded.
    """
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.STDOUT)

    process = subprocess.Popen(cmd, **kwargs)
    assert process.stdout is not None

    for raw_line in process.stdout:
        line = raw_line.decode(errors="replace")
        print(line, end="", flush=True)
        log_file.write(line)
        log_file.flush()

    process.wait()
    return subprocess.CompletedProcess(cmd, process.returncode)
