"""
Unit tests for MCP server tools.

Tests cover:
- get_repo_for_project: happy path, case-insensitivity, unknown key
- get_developer_rules: returns expected structure and mandatory rules
- get_project_setup: happy path, case-insensitivity, unknown key, required fields
"""

import json

import pytest

from mcp_server.server import (
    get_developer_rules,
    get_project_setup,
    get_repo_for_project,
)

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


# ---------------------------------------------------------------------------
# get_project_setup tests
# ---------------------------------------------------------------------------

_SAMPLE_SETUP = {
    "MYPROJ": {
        "platform": "python",
        "setup_commands": [
            "python -m venv venv",
            ". venv/bin/activate && pip install -e . -q",
        ],
        "test_command": ". venv/bin/activate && python -m pytest tests/ -q --tb=short",
        "lint_command": ". venv/bin/activate && pre-commit run --all-files",
        "vcs": {
            "branch_pattern": "feature/{ticket_key}+{slug}",
            "commit_template": "feat({ticket_key}): {summary}",
            "files_changed_command": "git diff --name-only HEAD",
            "push_command": "git push origin {branch}",
            "pr_command": "gh pr create --base main",
        },
    }
}


@pytest.fixture()
def setup_file(tmp_path, monkeypatch):
    """Write a minimal project-setup.json and patch _SETUP_FILE to point at it."""
    path = tmp_path / "project-setup.json"
    path.write_text(json.dumps(_SAMPLE_SETUP))

    import mcp_server.server as server_module

    monkeypatch.setattr(server_module, "_SETUP_FILE", path)
    return path


def test_get_project_setup_returns_python_setup(setup_file):
    result = get_project_setup("MYPROJ")
    assert result["platform"] == "python"
    assert isinstance(result["setup_commands"], list)
    assert len(result["setup_commands"]) > 0


def test_get_project_setup_has_required_fields(setup_file):
    result = get_project_setup("MYPROJ")
    for field in ("platform", "setup_commands", "test_command", "lint_command", "vcs"):
        assert field in result, f"Missing field '{field}' in get_project_setup response"


def test_get_project_setup_vcs_has_required_fields(setup_file):
    vcs = get_project_setup("MYPROJ")["vcs"]
    for key in (
        "branch_pattern",
        "commit_template",
        "files_changed_command",
        "push_command",
        "pr_command",
    ):
        assert key in vcs, f"Missing vcs key '{key}' in get_project_setup response"


def test_get_project_setup_case_insensitive(setup_file):
    lower = get_project_setup("myproj")
    upper = get_project_setup("MYPROJ")
    mixed = get_project_setup("MyProj")
    assert lower == upper == mixed


def test_get_project_setup_unknown_key_raises(setup_file):
    with pytest.raises(ValueError, match="No setup configuration"):
        get_project_setup("TOTALLY_UNKNOWN_XYZ_9999")


def test_get_project_setup_unknown_key_includes_known_projects(setup_file):
    with pytest.raises(ValueError, match="MYPROJ"):
        get_project_setup("UNKNOWN")
