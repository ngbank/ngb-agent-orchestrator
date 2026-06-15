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
        "temperature": 0,
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
def run_agent(user_prompt: str, max_steps: int = 6) -> str:
    """
    Run a ReAct loop with runtime-enforced tool boundaries.

    The system prompt stays intentionally open. The actual constraint is that
    the runtime only exposes one executable tool and blocks everything else.
    """
    api_key = required_env("AZURE_API_KEY")
    api_base = required_env("AZURE_FOUNDRY_API_BASE")
    model = resolve_model_name()

    # Keep the prompt broad on purpose: the demo should show that runtime tool
    # exposure, not prompt wording, determines what actions are possible.
    system_prompt = "You are a helpful assistant."

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    print("=" * 72)
    print("Constrained ReAct Agent Demo")
    print("Model:", model)
    print("Only tool available: show_alert(message)")
    print("=" * 72)

    for step in range(1, max_steps + 1):
        print(f"\n[Step {step}] Calling model...")
        response = call_foundry_chat(
            api_base=api_base,
            api_key=api_key,
            model=model,
            messages=messages,
        )

        choice = response["choices"][0]
        message = choice["message"]

        assistant_entry: dict[str, Any] = {"role": "assistant"}

        if "content" in message and message["content"] is not None:
            assistant_entry["content"] = message["content"]

        if "tool_calls" in message and message["tool_calls"]:
            assistant_entry["tool_calls"] = message["tool_calls"]
            messages.append(assistant_entry)

            for tool_call in message["tool_calls"]:
                tool_name = tool_call["function"]["name"]
                tool_args_raw = tool_call["function"].get("arguments", "{}")

                print(f"[Step {step}] Model requested tool: {tool_name}")
                print(f"[Step {step}] Tool args: {tool_args_raw}")

                # Runtime enforcement: block anything that is not show_alert.
                if tool_name != "show_alert":
                    tool_result = "Tool blocked by runtime policy: only show_alert is available."
                else:
                    try:
                        tool_args = json.loads(tool_args_raw)
                        alert_message = str(tool_args.get("message", "")).strip()
                        if not alert_message:
                            tool_result = "show_alert not executed: missing non-empty 'message'."
                        else:
                            tool_result = show_alert(alert_message)
                    except json.JSONDecodeError:
                        tool_result = "show_alert not executed: invalid JSON in tool arguments."

                print(f"[Step {step}] Tool result: {tool_result}")

                # Feed the observation back to the model.
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": tool_name,
                        "content": tool_result,
                    }
                )

            # Continue loop so the model can produce final user-facing text.
            continue

        # No tool call: this is the final answer.
        text = str(message.get("content", "")).strip()
        if not text:
            text = "Model returned no text."

        print(f"[Step {step}] Final response: {text}")
        return text

    return "Stopped after max steps without a final response."


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
    return parser.parse_args()


def main() -> int:
    # Load local .env if present so this script works out-of-the-box in this repo.
    load_dotenv_if_present(Path(__file__).resolve().parent / ".env")

    args = parse_args()

    try:
        final_text = run_agent(args.prompt, max_steps=args.max_steps)
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
