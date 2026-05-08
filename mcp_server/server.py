"""
MCP Server: Agent Harness

Exposes tools:
  - get_repo_for_project: resolves a JIRA project key to its Git repository URL
  - get_developer_rules: returns the standard developer rules enforced on every execution

Run with:
    python -m mcp_server.server

Or register in your MCP client config (e.g. goose, claude desktop) pointing at this module.
"""

import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Path to the mapping file, relative to this file's location (repo root/config/)
_MAPPING_FILE = Path(__file__).parent.parent / "config" / "project-repo-mapping.md"

mcp = FastMCP(
    name="agent-harness",
    instructions=(
        "Use get_repo_for_project to resolve a JIRA project key to its Git repository URL"
        " before cloning. "
        "Use get_developer_rules to retrieve the mandatory developer rules that must be"
        " followed during every execution session."
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

_DEVELOPER_RULES: list[dict[str, str]] = [
    {
        "id": "DR-001",
        "rule": "Run pre-commit hooks before every commit",
        "command": "pre-commit run --all-files",
        "rationale": (
            "Ensures code quality gates (linting, formatting, type checks) pass"
            " before changes enter version control."
        ),
    },
    {
        "id": "DR-002",
        "rule": "Never commit directly to main or master",
        "rationale": (
            "All changes must go through a feature branch and pull request to"
            " maintain code review and CI/CD integrity."
        ),
    },
    {
        "id": "DR-003",
        "rule": (
            "Feature branches must follow naming convention:"
            " feature/{TICKET-ID}+{summary-slug}"
        ),
        "rationale": (
            "Consistent branch naming links code changes back to JIRA tickets and"
            " makes history easy to navigate."
        ),
    },
    {
        "id": "DR-004",
        "rule": "Run the full test suite before committing",
        "command": "python -m pytest tests/ -q --tb=short",
        "rationale": "Prevents regressions from being committed.",
    },
]


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
    return _DEVELOPER_RULES


if __name__ == "__main__":
    mcp.run()
