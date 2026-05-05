# Project to Repository Mapping

This file maps JIRA project keys to their Git repository SSH URLs.
The MCP server (`mcp_server/server.py`) reads this file to resolve which repo to clone for a given ticket.

To add a new project, append a row to the table below.
The `Project Key` must match the JIRA project key exactly (case-insensitive lookup is applied at runtime).

| Project Key | Repository URL                                          | Description               |
|-------------|---------------------------------------------------------|---------------------------|
| AOS         | git@github.com-ngbank:ngbank/ngb-agent-orchestrator.git | Agent Orchestrator        |
