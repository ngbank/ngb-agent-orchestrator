"""Tests for the LiteLLM proxy OTel bootstrap module.

The module under test is intended to be imported once by the LiteLLM proxy
subprocess as ``litellm_settings.callbacks: otel.litellm_proxy_setup.proxy_handler_instance``.
Its import-time side-effects must:

* seed :mod:`otel.context` from ``NGB_WORKFLOW_ID`` / ``NGB_TICKET_KEY`` /
  ``NGB_WORKFLOW_STAGE`` so proxy-side ``OtelContext.capture()`` populates
  correlation attributes;
* call :func:`otel.instrumentation.setup_tracing` so the dispatcher's
  :class:`otel.exporters.LocalJsonFileExporter` is installed inside the
  subprocess and ``OtelLiteLLMCallback`` is registered with LiteLLM;
* expose :data:`otel.litellm_proxy_setup.proxy_handler_instance` as the YAML
  callback target (LiteLLM only loads one dotted-path callback per config),
  without double-registering ``otel_callback_instance``.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import litellm
import pytest
from litellm.integrations.custom_logger import CustomLogger

from otel.context import (
    _node_name,
    _ticket_key,
    _workflow_id,
    _workflow_stage,
    get_ticket_key,
    get_workflow_id,
    get_workflow_stage,
)


@pytest.fixture(autouse=True)
def _reset_context():
    """Reset OTel context vars before and after each test."""
    _workflow_id.set(None)
    _ticket_key.set(None)
    _node_name.set(None)
    _workflow_stage.set(None)
    yield
    _workflow_id.set(None)
    _ticket_key.set(None)
    _node_name.set(None)
    _workflow_stage.set(None)


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
    def test_proxy_handler_instance_is_placeholder(self):
        """proxy_handler_instance must be a valid callback target that does not
        double-register otel_callback_instance (see module docstring point 3)."""
        from otel.litellm_callback import otel_callback_instance

        mod, _ = _reload_module({"NGB_WORKFLOW_ID": None, "NGB_TICKET_KEY": None})
        assert mod.proxy_handler_instance is not otel_callback_instance
        assert isinstance(mod.proxy_handler_instance, CustomLogger)

    def test_no_double_registration_when_yaml_loader_appends_handler(self):
        """Simulates what LiteLLM's proxy YAML loader does after import: append
        the resolved ``callbacks:`` target to ``litellm.callbacks``. Because
        ``proxy_handler_instance`` is a distinct object from
        ``otel_callback_instance``, this must not duplicate the latter's entry."""
        from otel.litellm_callback import otel_callback_instance

        mod, _ = _reload_module({"NGB_WORKFLOW_ID": None, "NGB_TICKET_KEY": None})
        original_callbacks = list(litellm.callbacks)
        litellm.callbacks[:] = [otel_callback_instance]
        try:
            litellm.callbacks.append(mod.proxy_handler_instance)
            assert litellm.callbacks.count(otel_callback_instance) == 1
        finally:
            litellm.callbacks[:] = original_callbacks

    def test_seeds_context_from_env(self):
        mod, mock_setup = _reload_module(
            {
                "NGB_WORKFLOW_ID": "wf-aos118",
                "NGB_TICKET_KEY": "AOS-118",
                "NGB_WORKFLOW_STAGE": "plan",
            }
        )
        assert mod is not None
        assert get_workflow_id() == "wf-aos118"
        assert get_ticket_key() == "AOS-118"
        assert get_workflow_stage() == "plan"
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
        from otel.litellm_callback import otel_callback_instance

        mod, _ = _reload_module({"NGB_WORKFLOW_ID": "wf-1", "NGB_TICKET_KEY": "AOS-1"})
        with patch("otel.instrumentation.setup_tracing") as mock_setup_2:
            reloaded = importlib.reload(mod)
            mock_setup_2.assert_called_once()
        assert reloaded.proxy_handler_instance is not otel_callback_instance


class TestProxyYamlReferencesBootstrap:
    """Guard: the generated proxy YAML must route callbacks through this module."""

    def test_generated_yaml_uses_bootstrap_callback(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        from orchestrator.utils import _litellm_config_yaml

        yaml = _litellm_config_yaml("openai/gpt-4o")
        # List form is required so LiteLLM's YAML loader *extends* (not replaces)
        # litellm.callbacks, preserving the OtelLiteLLMCallback registered by
        # the bootstrap module during import.
        assert "callbacks:\n    - otel.litellm_proxy_setup.proxy_handler_instance" in yaml

    def test_generated_yaml_pins_request_timeout(self, monkeypatch):
        """A stalled upstream stream must be aborted well before the plan-phase
        5-minute ceiling. The proxy's `request_timeout` bounds any single LLM
        call so a runaway Kimi-K2.6 reasoning loop cannot burn ~10 min per try.
        """
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        from orchestrator.utils import _litellm_config_yaml

        yaml = _litellm_config_yaml("openai/gpt-4o")
        assert "request_timeout: 240" in yaml
