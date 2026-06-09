"""Regression tests for lazy imports in dispatcher.commands.common."""

import json
import subprocess
import sys


def _run_import_probe(module_name: str) -> dict:
    script = (
        "import importlib, json, sys\n"
        f"importlib.import_module('{module_name}')\n"
        "mods = set(sys.modules.keys())\n"
        "print(json.dumps({\n"
        "  'has_jira': 'dispatcher.jira_client' in mods,\n"
        "  'has_langgraph': any(m == 'langgraph' or m.startswith('langgraph.') for m in mods),\n"
        "  'has_graph_builder': 'graph.builder' in mods\n"
        "}))\n"
    )
    output = subprocess.check_output([sys.executable, "-c", script], text=True)
    return json.loads(output)


def test_common_import_does_not_load_jira_or_langgraph_or_graph_builder():
    probe = _run_import_probe("dispatcher.commands.common")
    assert probe["has_jira"] is False
    assert probe["has_langgraph"] is False
    assert probe["has_graph_builder"] is False


def test_constants_import_stays_lightweight():
    probe = _run_import_probe("dispatcher.constants")
    assert probe["has_jira"] is False
    assert probe["has_langgraph"] is False
    assert probe["has_graph_builder"] is False
