"""Tests for the LiteLLM proxy OTel bootstrap module.

The module under test is intended to be imported once by the LiteLLM proxy
subprocess as ``litellm_settings.callbacks: otel.litellm_proxy_setup.proxy_handler_instance``.
Its import-time side-effects must:

* seed :mod:`otel.context` from ``NGB_WORKFLOW_ID`` / ``NGB_TICKET_KEY``
  so proxy-side ``OtelContext.capture()`` populates correlation attributes;
* call :func:`otel.instrumentation.setup_tracing` so the dispatcher's
  :class:`otel.exporters.LocalJsonFileExporter` is installed inside the
  subprocess and ``OtelLiteLLMCallback`` is registered with LiteLLM;
* expose :data:`graph.litellm_callbacks.proxy_handler_instance` as the YAML
  callback target (LiteLLM only loads one dotted-path callback per config).
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from graph.litellm_callbacks import TokenUsageLogger
from graph.litellm_callbacks import proxy_handler_instance as token_logger_instance
from otel.context import (
    _node_name,
    _ticket_key,
    _workflow_id,
    get_ticket_key,
    get_workflow_id,
)


@pytest.fixture(autouse=True)
def _reset_context():
    """Reset OTel context vars before and after each test."""
    _workflow_id.set(None)
    _ticket_key.set(None)
    _node_name.set(None)
    yield
    _workflow_id.set(None)
    _ticket_key.set(None)
    _node_name.set(None)


def _reload_module(monkeypatch_env: dict[str, str | None]):
    """Reload ``otel.litellm_proxy_setup`` after applying ``monkeypatch_env``.

    Returns the reloaded module so callers can assert on its exports.
    Values of ``None`` in ``monkeypatch_env`` delete the env var.
    """
    import os

    saved: dict[str, str | None] = {}
    for k, v in monkeypatch_env.items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    try:
        with patch("otel.instrumentation.setup_tracing") as mock_setup:
            import otel.litellm_proxy_setup as mod  # noqa: WPS433

            reloaded = importlib.reload(mod)
        return reloaded, mock_setup
    finally:
        for k, original in saved.items():
            if original is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = original


class TestProxyOtelBootstrap:
    def test_reexports_token_usage_logger(self):
        mod, _ = _reload_module({"NGB_WORKFLOW_ID": None, "NGB_TICKET_KEY": None})
        assert mod.proxy_handler_instance is token_logger_instance
        assert isinstance(mod.proxy_handler_instance, TokenUsageLogger)

    def test_seeds_context_from_env(self):
        mod, mock_setup = _reload_module(
            {"NGB_WORKFLOW_ID": "wf-aos118", "NGB_TICKET_KEY": "AOS-118"}
        )
        assert mod is not None
        assert get_workflow_id() == "wf-aos118"
        assert get_ticket_key() == "AOS-118"
        mock_setup.assert_called_once()

    def test_calls_setup_tracing_even_without_env(self):
        # File routing should still be installed so spans land in
        # LOGS_DIR/unknown/otel.jsonl rather than nowhere.
        _, mock_setup = _reload_module({"NGB_WORKFLOW_ID": None, "NGB_TICKET_KEY": None})
        mock_setup.assert_called_once()
        assert get_workflow_id() is None
        assert get_ticket_key() is None

    def test_partial_env_only_ticket(self):
        _, mock_setup = _reload_module({"NGB_WORKFLOW_ID": None, "NGB_TICKET_KEY": "AOS-999"})
        assert get_ticket_key() == "AOS-999"
        assert get_workflow_id() is None
        mock_setup.assert_called_once()

    def test_bootstrap_function_is_idempotent(self):
        """Reloading the module a second time must not raise."""
        mod, _ = _reload_module({"NGB_WORKFLOW_ID": "wf-1", "NGB_TICKET_KEY": "AOS-1"})
        with patch("otel.instrumentation.setup_tracing") as mock_setup_2:
            reloaded = importlib.reload(mod)
            mock_setup_2.assert_called_once()
        assert reloaded.proxy_handler_instance is token_logger_instance


class TestProxyYamlReferencesBootstrap:
    """Guard: the generated proxy YAML must route callbacks through this module."""

    def test_generated_yaml_uses_bootstrap_callback(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        from graph.utils import _litellm_config_yaml

        yaml = _litellm_config_yaml("openai/gpt-4o")
        # List form is required so LiteLLM's YAML loader *extends* (not replaces)
        # litellm.callbacks, preserving the OtelLiteLLMCallback registered by
        # the bootstrap module during import.
        assert "callbacks:\n    - otel.litellm_proxy_setup.proxy_handler_instance" in yaml
