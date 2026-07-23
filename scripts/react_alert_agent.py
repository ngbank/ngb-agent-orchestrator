#!/usr/bin/env python3
"""
Minimal ReAct-style agent demo (single-file, single-tool, highly readable).

What this demonstrates:
1. The model can reason and suggest actions.
2. The runtime decides what tools exist.
3. With only one tool available, the agent can only do one real-world action:
   show a macOS alert.

This script uses Azure AI Foundry (Kimi) directly via HTTP (no LiteLLM).
It reads credentials from environment variables used in this repository:
- AZURE_API_KEY
- AZURE_FOUNDRY_API_BASE (example: https://<resource>.services.ai.azure.com/openai/v1)
- GOOSE_MODEL (expected: foundry/Kimi-K2.6)

If env vars are not loaded, it will also read a local .env file in this folder.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


# -----------------------------
# 1) Tiny .env loader (stdlib)
# -----------------------------
def load_dotenv_if_present(path: Path) -> None:
    """Load KEY=VALUE lines from .env into os.environ if key is not already set."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        # Remove optional surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        if key and key not in os.environ:
            os.environ[key] = value


# ---------------------------------------
# 2) Resolve config for Foundry + Kimi
# ---------------------------------------
def resolve_model_name() -> str:
    """
    Resolve the model deployment name for Foundry chat/completions.

    Priority:
    - REACT_MODEL (optional override)
    - GOOSE_MODEL (e.g. foundry/Kimi-K2.6 -> Kimi-K2.6)
    - fallback default: Kimi-K2.6
    """
    override = os.getenv("REACT_MODEL", "").strip()
    if override:
        return override

    goose_model = os.getenv("GOOSE_MODEL", "").strip()
    if goose_model:
        if "/" in goose_model:
            # foundry/Kimi-K2.6 -> Kimi-K2.6
            return goose_model.split("/", 1)[1]
        return goose_model

    return "Kimi-K2.6"


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing {name}. Load your .env (for example: direnv allow) or export it manually."
        )
    return value


def build_ssl_context() -> ssl.SSLContext:
    """
    Build TLS settings for outbound HTTPS calls.

    Defaults to skipping verification (for demo simplicity).
    For production use, opt-in to strict verification:
    - set REACT_STRICT_TLS=1 to enable certificate verification, or
    - set REACT_CA_BUNDLE to a PEM file with your trusted CA chain.
    """
    strict = os.getenv("REACT_STRICT_TLS", "").strip().lower()
    if strict not in {"1", "true", "yes", "on"}:
        # Default: skip verification for demo
        return ssl._create_unverified_context()

    ca_bundle = os.getenv("REACT_CA_BUNDLE", "").strip() or os.getenv("SSL_CERT_FILE", "").strip()
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)

    return ssl.create_default_context()


# ---------------------------------
# 3) The only tool this agent has
# ---------------------------------
def show_alert(message: str) -> str:
    """
    Show a macOS alert using AppleScript.

    This is the only side-effect action in the entire script.
    """
    # Use argv passing to avoid quote-escaping issues.
    script = (
        "on run argv\n"
        "  set msg to item 1 of argv\n"
        '  display alert "Agent Alert" message msg as informational\n'
        "end run"
    )

    completed = subprocess.run(
        ["osascript", "-e", script, message],
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        return f"show_alert failed: {stderr or 'unknown osascript error'}"

    return f"Alert displayed with message: {message!r}"


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "show_alert",
            "description": "Show a native macOS alert dialog with the provided message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The exact text to display in the alert.",
                    }
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    }
]


# ------------------------------------------------
# 4) Direct API call to Azure Foundry chat API
# ------------------------------------------------
def call_foundry_chat(
    *,
    api_base: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Call Foundry chat/completions with tool definitions."""
    url = f"{api_base.rstrip('/')}/chat/completions"
    ssl_context = build_ssl_context()

    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOL_DEFINITIONS,
        "tool_choice": "auto",
    }

    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "api-key": api_key,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=60, context=ssl_context) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from Foundry: {body}") from exc
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
            raise RuntimeError(
                "TLS certificate verification failed. "
                "To enable strict verification, set REACT_STRICT_TLS=1. "
                "To use a custom CA bundle, set REACT_CA_BUNDLE=/path/to/ca.pem."
            ) from exc
        raise RuntimeError(f"Network error talking to Foundry: {exc}") from exc


# ------------------------------------------------
# 5) ReAct loop: think -> maybe tool -> continue
# ------------------------------------------------

# Runtime policy: the set of tool names the agent is allowed to execute.
# Anything outside this set is rejected, even if the model asks for it.
ALLOWED_TOOL_NAMES: set[str] = {"show_alert"}


def run_agent(user_prompt: str, max_steps: int = 6, *, verbose: bool = False) -> str:
    """
    Run a ReAct loop with runtime-enforced tool boundaries.

    The loop body intentionally stays short so the control flow is obvious:
    think -> (optional) run tools -> repeat, or return the final answer.

    When ``verbose`` is True, the full message history sent to the model and
    the raw assistant message returned are printed each turn.
    """
    foundry = resolve_foundry_config()
    messages = build_initial_messages(user_prompt)
    print_demo_banner(foundry.model)
    dump_messages("Initial conversation", messages, verbose=verbose)

    for step in range(1, max_steps + 1):
        print(f"\n[Step {step}] Calling model...")
        dump_messages(f"Step {step} request messages", messages, verbose=verbose)

        message = request_next_message(foundry, messages)
        dump_message(f"Step {step} assistant response", message, verbose=verbose)

        tool_calls = tool_calls_from(message)
        if tool_calls:
            record_assistant_turn(messages, message)
            for tool_call in tool_calls:
                result = handle_tool_call(tool_call, step=step, verbose=verbose)
                record_tool_observation(messages, tool_call, result)
            continue  # let the model react to the tool observations

        return finalize_response(message, step=step)

    return "Stopped after max steps without a final response."


# --- setup helpers ---------------------------------------------------------


class FoundryConfig:
    """Bundle of values needed to call the Foundry chat endpoint."""

    __slots__ = ("api_base", "api_key", "model")

    def __init__(self, *, api_base: str, api_key: str, model: str) -> None:
        self.api_base = api_base
        self.api_key = api_key
        self.model = model


def resolve_foundry_config() -> FoundryConfig:
    return FoundryConfig(
        api_base=required_env("AZURE_FOUNDRY_API_BASE"),
        api_key=required_env("AZURE_API_KEY"),
        model=resolve_model_name(),
    )


def build_initial_messages(user_prompt: str) -> list[dict[str, Any]]:
    # Keep the prompt broad on purpose: the demo should show that runtime tool
    # exposure, not prompt wording, determines what actions are possible.
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": user_prompt},
    ]


def print_demo_banner(model: str) -> None:
    print("=" * 72)
    print("Constrained ReAct Agent Demo")
    print("Model:", model)
    print("Only tool available: show_alert(message)")
    print("=" * 72)


# --- model I/O -------------------------------------------------------------


def request_next_message(foundry: FoundryConfig, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Call the model once and return the assistant message dict."""
    response = call_foundry_chat(
        api_base=foundry.api_base,
        api_key=foundry.api_key,
        model=foundry.model,
        messages=messages,
    )
    return response["choices"][0]["message"]


def tool_calls_from(message: dict[str, Any]) -> list[dict[str, Any]]:
    return message.get("tool_calls") or []


def record_assistant_turn(messages: list[dict[str, Any]], message: dict[str, Any]) -> None:
    """Append the assistant message (content and/or tool_calls) to history."""
    entry: dict[str, Any] = {"role": "assistant"}
    if message.get("content") is not None:
        entry["content"] = message["content"]
    if message.get("tool_calls"):
        entry["tool_calls"] = message["tool_calls"]
    messages.append(entry)


def record_tool_observation(
    messages: list[dict[str, Any]], tool_call: dict[str, Any], result: str
) -> None:
    messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "name": tool_call["function"]["name"],
            "content": result,
        }
    )


def finalize_response(message: dict[str, Any], *, step: int) -> str:
    text = str(message.get("content", "")).strip() or "Model returned no text."
    print(f"[Step {step}] Final response: {text}")
    return text


# --- verbose tracing helpers ----------------------------------------------


def dump_messages(label: str, messages: list[dict[str, Any]], *, verbose: bool) -> None:
    """Pretty-print the full message history sent to the model."""
    if not verbose:
        return
    print(f"\n--- {label} ({len(messages)} message(s)) ---")
    for index, message in enumerate(messages):
        print(f"  [{index}] {json.dumps(message, indent=2, default=str)}")
    print("--- end ---")


def dump_message(label: str, message: dict[str, Any], *, verbose: bool) -> None:
    """Pretty-print a single assistant message returned by the model."""
    if not verbose:
        return
    print(f"\n--- {label} ---")
    print(json.dumps(message, indent=2, default=str))
    print("--- end ---")


# --- tool dispatch (policy + validation + execution) -----------------------


def handle_tool_call(tool_call: dict[str, Any], *, step: int, verbose: bool = False) -> str:
    """
    Single place where the runtime decides what to do with a model-requested
    tool call. The order makes the control points explicit:

      1. Tool allow-list check     (policy)
      2. Argument parsing/validation (input contract)
      3. Tool execution              (side effect)
    """
    name, raw_args = unpack_tool_call(tool_call)
    log_tool_request(step, name, raw_args)
    dump_message(f"Step {step} tool_call", tool_call, verbose=verbose)

    if not is_tool_allowed(name):
        result = reject_disallowed_tool(name)
    else:
        args, validation_error = validate_tool_arguments(name, raw_args)
        result = validation_error if validation_error else dispatch_tool(name, args)

    print(f"[Step {step}] Tool result: {result}")
    return result


def unpack_tool_call(tool_call: dict[str, Any]) -> tuple[str, str]:
    name = tool_call["function"]["name"]
    raw_args = tool_call["function"].get("arguments", "{}")
    return name, raw_args


def log_tool_request(step: int, name: str, raw_args: str) -> None:
    print(f"[Step {step}] Model requested tool: {name}")
    print(f"[Step {step}] Tool args: {raw_args}")


def is_tool_allowed(name: str) -> bool:
    return name in ALLOWED_TOOL_NAMES


def reject_disallowed_tool(name: str) -> str:
    return (
        f"Tool {name!r} blocked by runtime policy: "
        f"only {sorted(ALLOWED_TOOL_NAMES)} are available."
    )


def validate_tool_arguments(name: str, raw_args: str) -> tuple[dict[str, Any], str | None]:
    """Return (parsed_args, error_message). On failure parsed_args is empty."""
    try:
        parsed = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return {}, f"{name} not executed: invalid JSON in tool arguments."

    if name == "show_alert":
        message = str(parsed.get("message", "")).strip()
        if not message:
            return {}, "show_alert not executed: missing non-empty 'message'."
        return {"message": message}, None

    # Unknown tool slipped past the allow-list — defensive fallback.
    return parsed, None


def dispatch_tool(name: str, args: dict[str, Any]) -> str:
    if name == "show_alert":
        return show_alert(args["message"])
    return f"No handler registered for tool {name!r}."


# -----------------
# 6) CLI interface
# -----------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Single-file constrained ReAct demo using Azure Foundry Kimi and one tool: show_alert."
        ),
        epilog=(
            "Examples:\n"
            "  python scripts/react_alert_agent.py\n"
            "    → Uses default prompt: show an alert\n"
            "\n"
            '  python scripts/react_alert_agent.py "Send an email to my team"\n'
            "    → Demonstrates agent constraint: only show_alert tool available\n"
            "\n"
            '  python scripts/react_alert_agent.py "Show an alert: Build passed!" --max-steps 3\n'
            "    → Limit loop to 3 steps max\n"
            "\n"
            "Key concept: The agent is bound by the tools exposed by the runtime,\n"
            "not by the system prompt. No matter what you ask, only show_alert works."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Show an alert that says: Hello from the constrained agent demo!",
        help="User prompt for the agent.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=6,
        help="Maximum ReAct loop steps before stopping.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print the full message history sent to the model and the raw responses each turn.",
    )
    return parser.parse_args()


def main() -> int:
    # Load local .env if present so this script works out-of-the-box in this repo.
    load_dotenv_if_present(Path(__file__).resolve().parent / ".env")

    args = parse_args()

    try:
        final_text = run_agent(args.prompt, max_steps=args.max_steps, verbose=args.verbose)
        print("\n" + "-" * 72)
        print("Returned to user:")
        print(final_text)
        print("-" * 72)
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
