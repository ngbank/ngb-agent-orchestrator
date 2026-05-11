# Project Setup: Design Options

This document records the three approaches considered for providing
platform-specific project setup commands to the execute agent.

---

## Option 1 — Separate `get_project_setup` MCP tool ✅ (chosen)

**Approach**: Add a dedicated `get_project_setup(project_key)` MCP tool backed by a
`config/project-setup.json` file.

The recipe calls this tool in Step 2 and receives:
- `platform` — technology stack identifier
- `setup_commands` — ordered list of commands (with venv activation baked in)
- `test_command` — full test suite command (used in Step 5)
- `lint_command` — linter / pre-commit command (used in Step 6)

**Pros**:
- Clean separation of concerns — recipe instructions stay generic
- Agent gets authoritative, pre-validated commands without guessing
- Easy to add new projects: add a JSON entry, no recipe changes needed
- Fully testable — unit-tested in `tests/test_mcp_server.py`

**Cons**:
- Requires `config/project-setup.json` to be kept in sync with the project
- One additional MCP round-trip at startup

**Files**:
- `mcp_server/server.py` — `get_project_setup` tool
- `config/project-setup.json` — per-project setup config

---

## Option 2 — Auto-detect from repository files

**Approach**: The `get_project_setup` tool (or a new `detect_project_platform` tool)
inspects the checked-out repository for well-known files
(`pyproject.toml`, `requirements.txt`, `package.json`, `pom.xml`, `build.gradle`,
`Gemfile`) and infers the platform and standard commands automatically.

**Pros**:
- Zero configuration — works for any new project immediately
- Always reflects the actual repo state

**Cons**:
- Fragile — non-standard layouts break detection
- Agent must have the repo already cloned before the tool can run
- Hard to express project-specific overrides (e.g. custom test flags)
- Adds complexity to the MCP server

---

## Option 3 — Add a `platform` parameter to `get_developer_rules`

**Approach**: Extend the existing `get_developer_rules(platform?)` tool to return
platform-specific setup alongside the developer rules.

**Pros**:
- Fewer tools — agent only needs one call
- Simpler tool surface

**Cons**:
- Mixes two distinct concerns (developer rules vs environment setup) into one tool
- Platform is often not known to the caller at rules-fetch time (rules are fetched
  before repo inspection)
- Hard to support per-project overrides without adding more parameters
