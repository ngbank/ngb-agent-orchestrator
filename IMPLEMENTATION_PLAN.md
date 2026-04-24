# Implementation Plan: AOS-33 - Repo Setup and Local Dev Environment

**Status:** Draft  
**JIRA Ticket:** AOS-33  
**Priority:** P0  
**Estimate:** S (Small)

## Overview

Set up the ngb-agent-orchestrator repository with the required folder structure, Python environment, Goose integration, and comprehensive developer documentation to enable a 15-minute setup time for new developers.

## Implementation Steps

### 1. Repository Structure Setup

Create the following folder structure:

```
ngb-agent-orchestrator/
├── dispatcher/              # Python CLI and workflow logic
│   ├── __init__.py
│   └── cli.py              # Entry point for orchestration commands
├── recipes/                 # Goose YAML recipes
│   └── .gitkeep
├── schemas/                 # WorkPlan and validation JSON schemas
│   └── .gitkeep
├── state/                   # SQLite DB (gitignored)
│   └── .gitkeep
├── .env.example            # Credential template
├── .gitignore
├── requirements.txt        # Python dependencies
├── setup.py               # Package setup (optional)
└── README.md              # Setup and usage documentation
```

**Actions:**
- [ ] Create all directories with `.gitkeep` files where needed
- [ ] Initialize basic Python package structure in `dispatcher/`

### 2. Python Environment Setup

**Actions:**
- [ ] Create `requirements.txt` with dependencies:
  ```
  goose-ai
  python-dotenv
  pydantic
  click
  sqlalchemy
  jira
  ```
- [ ] Test virtual environment creation: `python3 -m venv venv`
- [ ] Verify activation works: `source venv/bin/activate`
- [ ] Test installation: `pip install -r requirements.txt`

### 3. Environment Configuration

**Actions:**
- [ ] Create `.env.example` with required variables:
  ```
  # JIRA Configuration
  JIRA_URL=https://mirandags.atlassian.net
  JIRA_EMAIL=your.email@example.com
  JIRA_API_TOKEN=your_api_token_here
  
  # Goose Configuration
  GOOSE_API_KEY=your_goose_api_key
  
  # Database
  DATABASE_PATH=./state/orchestrator.db
  ```
- [ ] Document how to obtain each credential in README

### 4. Git Configuration

**Actions:**
- [ ] Create `.gitignore` with:
  ```
  # Environment
  .env
  venv/
  __pycache__/
  *.pyc
  .Python
  
  # State
  state/
  *.db
  *.sqlite
  
  # IDE
  .vscode/
  .idea/
  *.swp
  *.swo
  
  # OS
  .DS_Store
  Thumbs.db
  ```

### 5. Goose Integration

**Actions:**
- [ ] Verify Goose installation after `pip install -r requirements.txt`
- [ ] Test `goose run` command availability
- [ ] Create sample recipe in `recipes/` for testing
- [ ] Document Goose usage in README

### 6. Documentation

Create comprehensive `README.md` with the following sections:

**Sections:**
- [ ] **Project Overview** - What this orchestrator does
- [ ] **Prerequisites** - Python 3.x, pip, git
- [ ] **Quick Start** - 15-minute setup guide:
  1. Clone the repo
  2. Create virtual environment
  3. Install dependencies
  4. Configure credentials
  5. Run test command
- [ ] **Folder Structure** - Explain each directory's purpose
- [ ] **Environment Variables** - Reference to `.env.example`
- [ ] **Usage** - How to run orchestration commands
- [ ] **Development** - How to contribute/extend
- [ ] **Troubleshooting** - Common issues and solutions
- [ ] **References** - Links to Confluence docs

### 7. Basic CLI Setup

**Actions:**
- [ ] Create `dispatcher/cli.py` with basic Click commands:
  ```python
  import click
  
  @click.group()
  def cli():
      """Agent Orchestrator CLI"""
      pass
  
  @cli.command()
  def status():
      """Check orchestrator status"""
      click.echo("Orchestrator is ready!")
  
  if __name__ == '__main__':
      cli()
  ```
- [ ] Add entry point to make it executable
- [ ] Test: `python dispatcher/cli.py status`

### 8. Validation & Testing

**Acceptance Criteria Validation:**
- [ ] Repo has agreed folder structure
- [ ] README.md documents all setup steps clearly
- [ ] `.env.example` lists all required variables
- [ ] `.env` and `state/` are in `.gitignore`
- [ ] Goose is installable and `goose run` is executable
- [ ] Fresh clone can be running in ≤15 minutes

**Test Procedure:**
1. Clone repo to a fresh directory
2. Time the setup process following README
3. Verify all commands work
4. Ensure credentials are not committed
5. Confirm state directory is ignored

## Dependencies

**None** - This is Phase 0 foundation work

## Confluence References

- [Delivery Plan: Phase 0 and Phase 1](https://mirandags.atlassian.net/wiki/spaces/AOS/pages/2752538)
- [Conceptual Deep Dive: Agentic Workflow Architecture](https://mirandags.atlassian.net/wiki/spaces/AOS/pages/2850817)

## Estimated Time

- Structure setup: 30 minutes
- Python/Goose setup: 30 minutes
- Documentation: 1 hour
- Testing & validation: 30 minutes
- **Total: ~2.5 hours**

## Success Criteria

✅ All acceptance criteria from AOS-33 are met  
✅ A new developer can clone and run locally in 15 minutes  
✅ No credentials or state files are committed to git  
✅ Documentation is clear and comprehensive  
✅ Basic CLI is functional and testable

## Next Steps After Completion

Once AOS-33 is complete:
1. Move ticket to "Done" status
2. Tag release as `v0.1.0-foundation`
3. Proceed to next Phase 0 ticket (dispatcher CLI implementation)
4. Share setup guide with team for feedback
