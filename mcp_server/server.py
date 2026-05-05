"""
MCP Server: Repo Lookup

Exposes a single tool — get_repo_for_project — that resolves a JIRA project key
to its Git repository URL using config/project-repo-mapping.md as the source of truth.

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
    name="repo-lookup",
    instructions=(
        "Use get_repo_for_project to resolve a JIRA project key to its Git repository URL"
        " before cloning."
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


if __name__ == "__main__":
    mcp.run()
