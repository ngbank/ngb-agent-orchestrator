"""
MCP Server: Agent Harness

Exposes tools:
  - get_repo_for_project: resolves a JIRA project key to its Git repository URL
  - get_developer_rules: returns the standard developer rules enforced on every execution
  - get_project_setup: returns platform-specific setup commands for a project

Run with:
    python -m mcp_server.server

Or register in your MCP client config (e.g. goose, claude desktop) pointing at this module.
"""

import json
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Path to the mapping file, relative to this file's location (repo root/config/)
_MAPPING_FILE = Path(__file__).parent.parent / "config" / "project-repo-mapping.md"
_RULES_FILE = Path(__file__).parent.parent / "config" / "developer-rules.json"
_SETUP_FILE = Path(__file__).parent.parent / "config" / "project-setup.json"

mcp = FastMCP(
    name="agent-harness",
    instructions=(
        "Use get_repo_for_project to resolve a JIRA project key to its Git repository URL"
        " before cloning. "
        "Use get_developer_rules to retrieve the mandatory developer rules that must be"
        " followed during every execution session. "
        "Use get_project_setup to retrieve platform-specific setup commands (install"
        " dependencies, run tests, run linter) for a given JIRA project key."
    ),
)


def _load_mapping() -> dict[str, str]:
    """
    Parse config/project-repo-mapping.md and return {project_key -> repo_url}.

    Expects a Markdown table with columns: Project Key | Repository URL | Description
    Lines that don't match the table row pattern are skipped.
    """
    text = _MAPPING_FILE.read_text()
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        # Match table data rows: | KEY | URL | ... |
        match = re.match(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
        if not match:
            continue
        key_col = match.group(1).strip()
        url_col = match.group(2).strip()
        # Skip header and separator rows
        if key_col.lower() in ("project key", "---", "") or url_col.startswith("-"):
            continue
        mapping[key_col.upper()] = url_col
    return mapping


@mcp.tool()
def get_repo_for_project(project_key: str) -> str:
    """
    Return the Git repository SSH URL for a given JIRA project key.

    Args:
        project_key: The JIRA project key, e.g. "AOS", "MINIBANK". Case-insensitive.

    Returns:
        The repository SSH URL string (e.g. "git@github.com:org/repo.git").

    Raises:
        ValueError: If the project key is not found in the mapping file.
    """
    mapping = _load_mapping()
    key = project_key.strip().upper()
    if key not in mapping:
        known = ", ".join(sorted(mapping.keys())) or "none configured"
        raise ValueError(
            f"No repository mapped for project '{project_key}'. "
            f"Known projects: {known}. "
            f"Add an entry to config/project-repo-mapping.md."
        )
    return mapping[key]


# ---------------------------------------------------------------------------
# Developer rules
# ---------------------------------------------------------------------------


@mcp.tool()
def get_developer_rules() -> list[dict[str, str]]:
    """
    Return the mandatory developer rules enforced on every execution session.

    Each rule is a dict with:
      - id: unique rule identifier (e.g. "DR-001")
      - rule: human-readable rule statement
      - command: (optional) shell command to run to satisfy the rule
      - rationale: why this rule exists

    Returns:
        List of rule dicts that the agent must honour during execution.
    """
    return json.loads(_RULES_FILE.read_text())


@mcp.tool()
def get_project_setup(project_key: str) -> dict:
    """
    Return platform-specific setup commands for a given JIRA project key.

    The returned dict contains:
      - platform: the technology stack (e.g. "python", "node", "java")
      - setup_commands: ordered list of shell commands to set up the environment
      - test_command: shell command to run the full test suite
      - lint_command: shell command to run the linter / pre-commit hooks
      - vcs: version control workflow config with keys:
          branch_pattern, commit_template, files_changed_command,
          push_command, pr_command

    All commands include any required environment activation (e.g. venv) as a
    prefix so they can be run directly without prior activation.

    Args:
        project_key: The JIRA project key, e.g. "AOS". Case-insensitive.

    Returns:
        Dict with platform, setup_commands, test_command, lint_command, vcs.

    Raises:
        ValueError: If no setup configuration is found for the project key.
    """
    key = project_key.strip().upper()
    config = json.loads(_SETUP_FILE.read_text())
    if key not in config:
        known = ", ".join(sorted(config.keys())) or "none configured"
        raise ValueError(
            f"No setup configuration for project '{project_key}'. "
            f"Known projects: {known}. "
            f"Add an entry to config/project-setup.json."
        )
    return config[key]


if __name__ == "__main__":
    mcp.run()
