"""
Unit tests for MCP server tools.

Tests cover:
- get_repo_for_project: happy path, case-insensitivity, unknown key
- get_developer_rules: returns expected structure and mandatory rules
"""

import pytest

from mcp_server.server import get_developer_rules, get_repo_for_project, _load_mapping


# ---------------------------------------------------------------------------
# get_developer_rules tests
# ---------------------------------------------------------------------------


def test_get_developer_rules_returns_list():
    rules = get_developer_rules()
    assert isinstance(rules, list)
    assert len(rules) > 0


def test_get_developer_rules_each_has_required_fields():
    rules = get_developer_rules()
    for rule in rules:
        assert "id" in rule, f"Rule missing 'id': {rule}"
        assert "rule" in rule, f"Rule missing 'rule': {rule}"
        assert "rationale" in rule, f"Rule missing 'rationale': {rule}"


def test_get_developer_rules_ids_are_unique():
    rules = get_developer_rules()
    ids = [r["id"] for r in rules]
    assert len(ids) == len(set(ids)), "Rule IDs are not unique"


def test_get_developer_rules_includes_precommit_rule():
    """DR-001: pre-commit hooks must be run before committing."""
    rules = get_developer_rules()
    dr001 = next((r for r in rules if r["id"] == "DR-001"), None)
    assert dr001 is not None, "DR-001 (pre-commit) rule is missing"
    assert "pre-commit run --all-files" in dr001.get("command", ""), (
        "DR-001 must specify the pre-commit command"
    )


def test_get_developer_rules_includes_no_main_commit_rule():
    """DR-002: never commit directly to main or master."""
    rules = get_developer_rules()
    dr002 = next((r for r in rules if r["id"] == "DR-002"), None)
    assert dr002 is not None, "DR-002 (no commit to main) rule is missing"


def test_get_developer_rules_includes_branch_naming_rule():
    """DR-003: feature branch naming convention."""
    rules = get_developer_rules()
    dr003 = next((r for r in rules if r["id"] == "DR-003"), None)
    assert dr003 is not None, "DR-003 (branch naming) rule is missing"
    assert "feature/" in dr003["rule"], "DR-003 must reference 'feature/' convention"


def test_get_developer_rules_includes_test_suite_rule():
    """DR-004: run full test suite before committing."""
    rules = get_developer_rules()
    dr004 = next((r for r in rules if r["id"] == "DR-004"), None)
    assert dr004 is not None, "DR-004 (run tests) rule is missing"
    assert "pytest" in dr004.get("command", ""), (
        "DR-004 must specify the pytest command"
    )


def test_get_developer_rules_returns_new_list_each_call():
    """Mutations on the returned list must not affect subsequent calls."""
    rules1 = get_developer_rules()
    rules1.clear()
    rules2 = get_developer_rules()
    assert len(rules2) > 0, "get_developer_rules must return a fresh list each call"


# ---------------------------------------------------------------------------
# get_repo_for_project tests (smoke — mapping file must exist)
# ---------------------------------------------------------------------------


def test_get_repo_for_project_unknown_key_raises():
    with pytest.raises(ValueError, match="No repository mapped"):
        get_repo_for_project("TOTALLY_UNKNOWN_XYZ_9999")


def test_get_repo_for_project_case_insensitive(tmp_path, monkeypatch):
    mapping_file = tmp_path / "project-repo-mapping.md"
    mapping_file.write_text(
        "| Project Key | Repository URL | Description |\n"
        "|---|---|---|\n"
        "| MYPROJ | git@github.com:org/repo.git | Test |\n"
    )

    import mcp_server.server as server_module

    monkeypatch.setattr(server_module, "_MAPPING_FILE", mapping_file)

    assert get_repo_for_project("myproj") == "git@github.com:org/repo.git"
    assert get_repo_for_project("MYPROJ") == "git@github.com:org/repo.git"
    assert get_repo_for_project("MyProj") == "git@github.com:org/repo.git"


def test_get_repo_for_project_returns_url(tmp_path, monkeypatch):
    mapping_file = tmp_path / "project-repo-mapping.md"
    mapping_file.write_text(
        "| Project Key | Repository URL | Description |\n"
        "|---|---|---|\n"
        "| AOS | git@github.com:ngbank/ngb-agent-orchestrator.git | Orchestrator |\n"
    )

    import mcp_server.server as server_module

    monkeypatch.setattr(server_module, "_MAPPING_FILE", mapping_file)

    url = get_repo_for_project("AOS")
    assert url == "git@github.com:ngbank/ngb-agent-orchestrator.git"


def test_get_repo_for_project_unknown_key_includes_known_projects(tmp_path, monkeypatch):
    mapping_file = tmp_path / "project-repo-mapping.md"
    mapping_file.write_text(
        "| Project Key | Repository URL | Description |\n"
        "|---|---|---|\n"
        "| AOS | git@github.com:ngbank/repo.git | Test |\n"
    )

    import mcp_server.server as server_module

    monkeypatch.setattr(server_module, "_MAPPING_FILE", mapping_file)

    with pytest.raises(ValueError, match="AOS"):
        get_repo_for_project("UNKNOWN")
