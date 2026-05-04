# NGB Agent Orchestrator

**Agent Orchestration System with Goose Integration**

A Python-based orchestration framework for managing agentic workflows, integrating with JIRA for task management and Goose for AI-powered automation.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Quick Start (15-Minute Setup)](#quick-start-15-minute-setup)
- [Folder Structure](#folder-structure)
- [Configuration](#configuration)
- [Usage](#usage)
- [State Store](#state-store)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [References](#references)

---

## 🎯 Overview

The NGB Agent Orchestrator provides a structured framework for:

- **Workflow Orchestration**: Manage complex agentic workflows with state tracking
- **JIRA Integration**: Seamless integration with JIRA for task and project management
- **Goose Recipes**: Execute AI-powered automation recipes using Goose
- **State Management**: SQLite-based persistence for workflow state
- **Schema Validation**: JSON schema validation for WorkPlans and workflows

---

## ✅ Prerequisites

Before you begin, ensure you have the following installed:

- **Python 3.9+** (check with `python3 --version`)
- **pip** (Python package manager)
- **Git** (for cloning the repository)
- **JIRA Account** with API access to `mirandags.atlassian.net`

---

## 🚀 Quick Start (15-Minute Setup)

Follow these steps to get the orchestrator running locally:

### Step 1: Clone the Repository (1 min)

```bash
git clone <repository-url>
cd ngb-agent-orchestrator
```

### Step 2: Create Virtual Environment (2 min)

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate  # On macOS/Linux
# OR
venv\Scripts\activate     # On Windows
```

### Step 3: Install Dependencies (3 min)

```bash
# Install core dependencies
pip install -r requirements.txt

# Optional: Install development dependencies (for testing)
pip install -r requirements-dev.txt
```

This installs:
- Click (CLI framework)
- python-dotenv (environment management)
- Pydantic (data validation)
- SQLAlchemy (database ORM)
- jira (JIRA API client)
- litellm (model abstraction proxy layer)
- goose-ai (Goose integration)

**Development dependencies** (optional):
- pytest (testing framework)
- pytest-cov (coverage reporting)
- pytest-mock (mocking utilities)
- black, flake8, mypy, isort (code quality tools)

### Step 3.5: Start the LiteLLM Proxy

LiteLLM provides a unified OpenAI-compatible API endpoint that routes model calls to your chosen LLM backend (Anthropic, OpenAI, etc.). The proxy **must be running** before any `goose run` command is executed.

```bash
# Open a dedicated terminal and start the proxy
litellm --config config/litellm.yaml --port 4000
```

You should see:
```
LiteLLM: Proxy running on http://0.0.0.0:4000
```

> **Tip:** Keep this terminal open. All Goose recipe invocations route model calls through this proxy.

#### Calling the Proxy Manually

**Authentication** depends on whether `LITELLM_MASTER_KEY` is set in your `.env`:

- **With `LITELLM_MASTER_KEY` set** (default) — all requests require an `Authorization` header:

  ```bash
  # List registered models
  curl http://localhost:4000/models \
    -H "Authorization: Bearer sk-local-master-key"

  # Send a chat request to a specific model
  curl http://localhost:4000/v1/chat/completions \
    -H "Authorization: Bearer sk-local-master-key" \
    -H "Content-Type: application/json" \
    -d '{"model": "azure-gpt4", "messages": [{"role": "user", "content": "Hello"}]}'
  ```

- **Without `LITELLM_MASTER_KEY`** (remove it from `.env`) — no auth required:

  ```bash
  curl http://localhost:4000/models
  ```

Available model names (defined in `config/litellm.yaml`): `azure-gpt4` | `claude` | `gpt4o`

---

### Step 4: Configure Environment (5 min)

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your credentials
# Use your preferred editor (nano, vim, code, etc.)
nano .env
```

**Required Configuration:**

1. **JIRA_URL**: Your JIRA instance URL (default: `https://mirandags.atlassian.net`)
2. **JIRA_EMAIL**: Your JIRA email address
3. **JIRA_API_TOKEN**: Generate at [Atlassian API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
4. **LITELLM_MASTER_KEY**: Auth key for the local LiteLLM proxy (any string, e.g. `sk-local-master-key`)
5. **LITELLM_MODEL**: Model identifier for your chosen backend (e.g. `anthropic/claude-3-5-sonnet-20241022`)
6. **ANTHROPIC_API_KEY** / **OPENAI_API_KEY**: API key for your chosen LLM provider

### Step 5: Initialize and Verify (4 min)

```bash
# Initialize the orchestrator
python dispatcher/cli.py init

# Check status
python dispatcher/cli.py status
```

You should see:
```
🚀 Agent Orchestrator Status
==================================================
✅ Environment file (.env) found
✅ Directory 'dispatcher/' exists
✅ Directory 'recipes/' exists
✅ Directory 'schemas/' exists
✅ Directory 'state/' exists
✅ JIRA URL configured: https://mirandags.atlassian.net
==================================================
Orchestrator is ready! ✨
```

### Step 6: Test Goose (Optional)

```bash
# Verify Goose is installed
goose --version

# List available recipes
python dispatcher/cli.py goose
```

---

## 📁 Folder Structure

```
ngb-agent-orchestrator/
├── dispatcher/              # Python CLI and workflow orchestration logic
│   ├── __init__.py         # Package initialization
│   ├── cli.py              # Main CLI entry point
│   └── jira_client.py      # JIRA ticket fetching module
├── recipes/                 # Goose YAML recipe files
│   └── .gitkeep            # Keeps directory in git
├── schemas/                 # JSON schemas for WorkPlan and validation
│   └── .gitkeep
├── state/                   # SQLite database for orchestration state
│   └── .gitkeep            # (directory is gitignored)
├── tests/                   # Test suite
│   ├── __init__.py         # Test package initialization
│   └── test_jira_client.py # JIRA client tests
├── .env.example            # Template for environment variables
├── .env                    # Your local credentials (DO NOT COMMIT)
├── .gitignore              # Git ignore rules
├── requirements.txt        # Python dependencies
├── requirements-dev.txt    # Development/testing dependencies
└── README.md               # This file
```

### Directory Purposes

| Directory    | Purpose                                                    |
|--------------|------------------------------------------------------------|
| `dispatcher/` | Core Python orchestration logic, CLI commands, and JIRA integration |
| `recipes/`    | Goose YAML recipes for AI-powered automation workflows   |
| `schemas/`    | JSON schemas for validating WorkPlans and workflow data   |
| `state/`      | SQLite database files for persistent state (gitignored)   |
| `tests/`      | Comprehensive test suite for all modules                  |

---

## ⚙️ Configuration

### Environment Variables

All configuration is managed through the `.env` file. **Never commit this file to version control.**

| Variable              | Description                                          | Required | Example                                        |
|-----------------------|------------------------------------------------------|----------|------------------------------------------------|
| `JIRA_URL`            | Your JIRA Cloud instance URL                         | Yes      | `https://mirandags.atlassian.net`              |
| `JIRA_EMAIL`          | Your JIRA account email                              | Yes      | `user@example.com`                             |
| `JIRA_API_TOKEN`      | JIRA API token for authentication                    | Yes      | `ATATxxx...`                                   |
| `LITELLM_MASTER_KEY`  | Auth key for the LiteLLM proxy API                   | Yes      | `sk-local-master-key`                          |
| `LITELLM_MODEL`       | Model identifier passed to the provider              | Yes      | `anthropic/claude-3-5-sonnet-20241022`         |
| `LITELLM_BASE_URL`    | URL of the running LiteLLM proxy                     | Yes      | `http://localhost:4000`                        |
| `ANTHROPIC_API_KEY`   | Anthropic API key (when using Anthropic backend)     | Provider | `sk-ant-...`                                   |
| `OPENAI_API_KEY`      | OpenAI API key (when using OpenAI backend)           | Provider | `sk-...`                                       |
| `GOOSE_PROVIDER`      | Goose model provider — point at LiteLLM              | Yes      | `openai`                                       |
| `GOOSE_MODEL`         | Model name Goose uses (matches `model_name` in yaml) | Yes      | `default`                                      |
| `GOOSE_BASE_URL`      | Goose model base URL — point at LiteLLM proxy        | Yes      | `http://localhost:4000`                        |
| `DATABASE_PATH`       | Path to SQLite database                              | Optional | `./state/orchestrator.db`                      |
| `DEFAULT_PROJECT_KEY` | Default JIRA project key                             | Optional | `AOS`                                          |
| `LOG_LEVEL`           | Logging level                                        | Optional | `INFO` / `DEBUG` / `ERROR`                     |

### Obtaining JIRA Credentials

1. **JIRA_EMAIL**: Your Atlassian account email
2. **JIRA_API_TOKEN**:
   - Go to [Atlassian Account Security](https://id.atlassian.com/manage-profile/security/api-tokens)
   - Click "Create API token"
   - Give it a label (e.g., "Agent Orchestrator")
   - Copy the token to your `.env` file

---

## 🎮 Usage

### CLI Commands

The orchestrator provides a command-line interface via `dispatcher/cli.py`:

#### Check Status

```bash
python dispatcher/cli.py status
```

Displays:
- Environment configuration status
- Directory structure validation
- JIRA connection status

#### Initialize Environment

```bash
python dispatcher/cli.py init
```

- Creates `.env` from `.env.example`
- Sets up required directories
- Prepares the orchestrator for first use

#### Execute Goose Recipes

```bash
# List available recipes
python dispatcher/cli.py goose

# Run a specific recipe
python dispatcher/cli.py goose --recipe <recipe-name>
```

##### WorkPlan Generation Recipe

The `recipes/plan.yaml` recipe generates a WorkPlan JSON document from a JIRA ticket.

**Prerequisites:**
- Goose CLI installed (`~/.local/bin/goose`)
- LiteLLM proxy running on `http://localhost:4000`
- `acli` configured with JIRA credentials

**Start LiteLLM Proxy:**
```bash
# In a separate terminal
source venv/bin/activate
litellm --config config/litellm.yaml --port 4000
```

**Run the Recipe:**
```bash
# Add goose to PATH if needed
export PATH="$HOME/.local/bin:$PATH"

# Generate WorkPlan for a JIRA ticket
goose run --recipe recipes/plan.yaml \
  --params ticket_key=AOS-48 \
  --params output_path=workplans/AOS-48-plan.json
```

**What the Recipe Does:**
1. Fetches JIRA ticket data using `acli jira workitem view`
2. Analyzes repository structure to identify affected files
3. Generates WorkPlan JSON using azure-gpt4 LLM
4. Validates JSON against WorkPlan schema
5. Retries up to 3 times if validation fails (with intelligent error fixing)
6. Writes validated JSON to the specified output path

**Output Example:**
```json
{
  "schema_version": "1.0",
  "ticket_key": "AOS-48",
  "summary": "Add JWT authentication middleware to dashboard API",
  "approach": "Implement authentication middleware...",
  "tasks": [
    {
      "id": 1,
      "description": "Create authentication middleware",
      "files_likely_affected": ["api/middleware/auth.py"]
    }
  ],
  "risks": ["Integration with existing JWT service may require changes"],
  "questions_for_reviewer": [],
  "status": "pass"
}
```

**Status Values:**
- `pass`: Clear ticket, ready to proceed
- `concerns`: Implementable but has risks
- `blocked`: Ticket needs clarification (see `questions_for_reviewer`)

#### Get Help

```bash
# General help
python dispatcher/cli.py --help

# Command-specific help
python dispatcher/cli.py status --help
```

---

### Workflow Orchestration

The orchestrator provides a main dispatcher entry point for executing complete workflows from JIRA tickets.

#### Run a Workflow

Execute a complete workflow for a JIRA ticket:

```bash
# Set PYTHONPATH to enable imports
export PYTHONPATH=.

# Run a workflow
python dispatcher/run.py --ticket AOS-36
```

Output:
```
🚀 Starting workflow for ticket: AOS-36
🔧 Initializing JIRA client...
📥 Fetching ticket AOS-36...
✅ Ticket fetched: CLI Dispatcher Entry Point
🔍 Checking for duplicate workflows...
📝 Creating workflow record...
✅ Workflow created: b04fd4e0-1edc-4f95-8489-da914470b58d
⚙️  Executing workflow stages...
📋 Workflow created for ticket AOS-36
   Title: CLI Dispatcher Entry Point
   Status: In Progress
✅ Workflow stages completed (placeholder)
🎉 Workflow completed successfully
```

#### Dry Run Mode

Preview what would happen without executing any changes:

```bash
python dispatcher/run.py --ticket AOS-36 --dry-run
```

Output:
```
🚀 Starting workflow for ticket: AOS-36
[DRY RUN] Mode enabled - no changes will be made
[DRY RUN] Would fetch ticket: AOS-36
[DRY RUN] Would check for duplicate workflows
[DRY RUN] Would create workflow for ticket: AOS-36
[DRY RUN] Would execute workflow stages
[DRY RUN] Would transition to completed
✅ Dry run completed successfully
```

**Dry-run mode:**
- Validates JIRA configuration
- Does NOT call JIRA API
- Does NOT create database records
- Does NOT execute workflow stages
- Perfect for testing and validation

#### Duplicate Detection

The dispatcher prevents running multiple workflows for the same ticket simultaneously:

```bash
# First run - succeeds
python dispatcher/run.py --ticket AOS-36

# Second run while first is in-progress - rejected
python dispatcher/run.py --ticket AOS-36
# Output: ❌ Workflow already in progress for AOS-36
```

**Re-running completed workflows:**
- Completed workflows can be re-run
- A warning is shown indicating previous runs exist
- Each run creates a separate workflow record with full audit trail

#### Workflow Lifecycle

Workflows progress through the following states:

1. **`pending`** - Workflow created, not yet started
2. **`in_progress`** - Actively executing stages
3. **`completed`** - Successfully finished all stages
4. **`failed`** - Encountered unrecoverable error

All state transitions are logged to the audit log with timestamps, actor, and reason.

#### Error Handling

The dispatcher provides comprehensive error handling:

```bash
# Invalid ticket format
python dispatcher/run.py --ticket invalid
# Output: ❌ Invalid ticket format. Expected format: PROJECT-123

# Ticket not found
python dispatcher/run.py --ticket NOTFOUND-999
# Output: ❌ Ticket not found: Issue does not exist

# Missing JIRA credentials
python dispatcher/run.py --ticket AOS-36
# Output: ❌ JIRA configuration error: Missing required environment variables
```

**Exception behavior:**
- Configuration errors: Exit without creating workflow
- Ticket errors: Create workflow, mark as failed, log to audit
- Runtime errors: Mark workflow as failed, log exception details
- Keyboard interrupt (Ctrl+C): Mark workflow as failed, clean exit

---

### JIRA Client Module

The orchestrator includes a Python module for fetching JIRA tickets programmatically. This module provides structured access to JIRA ticket data for building planning context and workflow automation.

#### Basic Usage

```python
from dispatcher.jira_client import get_ticket, JiraTicketNotFoundError

try:
    # Fetch a JIRA ticket by key
    ticket = get_ticket('AOS-34')

    # Access ticket fields
    print(f"Title: {ticket.title}")
    print(f"Status: {ticket.status}")
    print(f"Description: {ticket.description}")
    print(f"Labels: {', '.join(ticket.labels)}")

except JiraTicketNotFoundError as e:
    print(f"Error: {e}")
```

#### Ticket Data Structure

The `JiraTicket` dataclass provides structured access to ticket information:

```python
@dataclass
class JiraTicket:
    key: str              # Ticket key (e.g., 'AOS-34')
    title: str            # Ticket summary/title
    description: str      # Full ticket description
    labels: List[str]     # List of labels
    status: str           # Current status (e.g., 'To Do', 'In Progress')
```

#### Advanced Usage

For more control, use the `JiraClient` class directly:

```python
from dispatcher.jira_client import JiraClient, JiraConfigurationError

try:
    # Create a client instance
    client = JiraClient()

    # Fetch multiple tickets
    ticket1 = client.get_ticket('AOS-34')
    ticket2 = client.get_ticket('AOS-35')

    # Process tickets
    for ticket in [ticket1, ticket2]:
        print(f"{ticket.key}: {ticket.title} [{ticket.status}]")

except JiraConfigurationError as e:
    print(f"Configuration error: {e}")
```

#### Error Handling

The JIRA client provides specific exception types for different error scenarios:

- **`JiraConfigurationError`**: Missing or invalid environment variables
- **`JiraAuthenticationError`**: Authentication failures or invalid credentials
- **`JiraTicketNotFoundError`**: Ticket does not exist (404)

```python
from dispatcher.jira_client import (
    get_ticket,
    JiraConfigurationError,
    JiraAuthenticationError,
    JiraTicketNotFoundError
)

try:
    ticket = get_ticket('INVALID-999')
except JiraConfigurationError as e:
    # Handle missing JIRA_URL, JIRA_EMAIL, or JIRA_API_TOKEN
    print(f"Configuration error: {e}")
except JiraAuthenticationError as e:
    # Handle authentication failures
    print(f"Authentication error: {e}")
except JiraTicketNotFoundError as e:
    # Handle non-existent tickets
    print(f"Ticket not found: {e}")
```

#### Requirements

The JIRA client requires the following environment variables to be set in your `.env` file:

- `JIRA_URL`: Your JIRA instance URL
- `JIRA_EMAIL`: Your JIRA account email
- `JIRA_API_TOKEN`: Your JIRA API token

See the [Configuration](#configuration) section for details on obtaining these credentials.

---

## � State Store

The orchestrator includes a SQLite-based state tracking system for workflow execution. Each workflow record maps to one JIRA ticket run and stores status, timestamps, work plan (JSON blob), and PR URL.

### Features

- **Persistent State**: SQLite database for reliable state storage
- **Audit Trail**: Append-only audit log for complete traceability
- **Idempotent Migrations**: Safe schema updates
- **Environment Configuration**: Flexible database path via `DB_PATH` env variable

### Quick Start

```python
from state import create_workflow, update_status, get_workflow

# Create a new workflow for a JIRA ticket
workflow_id = create_workflow(
    ticket_key="AOS-35",
    work_plan={"tasks": ["task1", "task2"]},
    status="pending"
)

# Update status (creates audit log entry)
update_status(
    workflow_id=workflow_id,
    status="in_progress",
    actor="copilot"
)

# Retrieve workflow
workflow = get_workflow(workflow_id)
print(f"Status: {workflow['status']}")
```

### Configuration

Set the database path in your `.env` file:

```bash
DB_PATH=state/local.db
```

If not set, defaults to `state/local.db`.

### Complete Documentation

For detailed API documentation, schema details, and advanced usage, see [state/README.md](state/README.md).

---

## �🛠️ Development

### Running Tests

The project includes a comprehensive test suite for the JIRA client module:

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_jira_client.py -v

# Run with coverage report
pytest tests/ --cov=dispatcher --cov-report=term
```

The test suite includes:
- ✅ Happy path scenarios with complete ticket data
- ✅ Edge cases (empty descriptions, missing labels)
- ✅ Error handling (404, authentication failures)
- ✅ Configuration validation

### Adding New CLI Commands

Edit `dispatcher/cli.py` and add a new Click command:

```python
@cli.command()
@click.option("--option", help="Description")
def my_command(option):
    """Command description."""
    click.echo(f"Running my_command with {option}")
```

### Creating Goose Recipes

Goose recipes are YAML files that define reusable automation workflows. See `recipes/plan.yaml` for a complete example.

**Basic Recipe Structure:**
```yaml
version: "1.0.0"
title: "Recipe Title"
description: "What the recipe does"

parameters:
  - key: param_name
    input_type: string
    requirement: required
    description: "Parameter description"

extensions:
  - type: builtin
    name: developer
    timeout: 300
    bundled: true

settings:
  goose_provider: "openai"
  goose_model: "azure-gpt4"
  temperature: 0.3

prompt: |
  Your instructions here...
  Use {{ param_name }} for parameter substitution.

retry:
  max_retries: 3
  checks:
    - type: shell
      command: "validation command here"
```

**Key Features:**
- **Parameters**: Define inputs with types (string, number, file, select, etc.)
- **Extensions**: Specify MCP servers and built-in tools (developer, file system, etc.)
- **Settings**: Configure LLM provider, model, and temperature
- **Retry Logic**: Automatic retry with validation checks
- **Response Schema**: Enforce structured JSON output

**See Also:**
- [Goose Recipes Documentation](https://goose-docs.ai/docs/tutorials/recipes-tutorial)
- [Recipe Reference](https://goose-docs.ai/docs/guides/recipes/recipe-reference)

### Managing State

The orchestrator uses SQLite for state management. Database files are stored in `state/` and are automatically excluded from git.

---

## 🔧 Troubleshooting

### Virtual Environment Not Activated

**Symptom**: Commands fail with "module not found" errors

**Solution**: Ensure your virtual environment is activated:
```bash
source venv/bin/activate  # macOS/Linux
venv\Scripts\activate     # Windows
```

You should see `(venv)` in your terminal prompt.

### JIRA Authentication Fails

**Symptom**: "Authentication failed" or 401 errors

**Solutions**:
1. Verify your `JIRA_EMAIL` matches your Atlassian account
2. Check that `JIRA_API_TOKEN` is correct (no extra spaces)
3. Ensure the API token hasn't expired
4. Test credentials at [Atlassian Account](https://id.atlassian.com)

### Goose Command Not Found

**Symptom**: `goose: command not found`

**Solution**:
```bash
# Ensure dependencies are installed
pip install -r requirements.txt

# Verify Goose installation
pip show goose-ai
```

### Permission Denied on state/ Directory

**Symptom**: Cannot write to `state/` directory

**Solution**:
```bash
# Ensure directory exists and is writable
mkdir -p state
chmod 755 state
```

### Python Version Issues

**Symptom**: Syntax errors or compatibility issues

**Solution**: Ensure you're using Python 3.9 or higher:
```bash
python3 --version  # Should show 3.9+
```

---

## 📚 References

### Confluence Documentation

- [Delivery Plan: Phase 0 and Phase 1](https://mirandags.atlassian.net/wiki/spaces/AOS/pages/2752538)
- [Conceptual Deep Dive: Agentic Workflow Architecture](https://mirandags.atlassian.net/wiki/spaces/AOS/pages/2850817)

### Related JIRA Tickets

- **AOS-33**: Repo Setup and Local Dev Environment (this implementation)

### External Resources

- [Click Documentation](https://click.palletsprojects.com/)
- [JIRA Python API](https://jira.readthedocs.io/)
- [Goose Documentation](https://docs.goose.ai/)
- [SQLAlchemy Documentation](https://www.sqlalchemy.org/)

---

## 📝 License

[Add your license information here]

---

## 🤝 Contributing

[Add contribution guidelines here]

---

**Questions or Issues?** Contact the Agent OS team or create a JIRA ticket in the AOS project.
