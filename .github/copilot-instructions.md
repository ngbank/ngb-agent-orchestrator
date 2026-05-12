# Repository Workflow Instructions

Follow these rules when working in this repository.

## Feature Branch Workflow

### Implementation Plan Workflow

When creating an implementation plan for a ticket:

1. **Present the plan in chat** — do not create a file
2. **Review and agree**: Discuss and refine the plan with the user
3. **Once the plan is agreed upon**, follow the steps below to start work

### Before Starting Work on a Ticket

After agreeing on an implementation plan, complete these steps in order:

1. **Check if the JIRA ticket description already contains the plan**
   - Run `acli jira workitem view TICKET-ID` and inspect the description
   - If the description is empty or doesn't contain the plan, update it:
     ```bash
     acli jira workitem edit --key "TICKET-ID" --description "<plan content>" -y
     ```
   - If the description already has the plan, skip this step

2. **Create a feature branch** using the naming convention: `feature/{jira_id}+{summary}`
   ```bash
   git checkout -b feature/TICKET-ID+brief-summary
   ```
   - Example: `feature/AOS-35+sqlite-workflow-state`
   - The branch name MUST include both the JIRA ticket ID and a brief summary

4. **Update the JIRA ticket**:
   - Assign the ticket to yourself
   - Transition the ticket status to "In Progress"
   ```bash
   acli jira workitem assign --key "TICKET-ID" --assignee "@me" -y
   acli jira workitem transition --key "TICKET-ID" --status "In Progress" -y
   ```

5. **Execute the implementation plan** by working through each task systematically

### Documentation Updates During Implementation

The `docs/` folder is the source of truth for all detailed documentation. The `README.md` is a concise project overview and setup guide only — detailed content belongs in `docs/`.

**When to update `docs/`:**
- Adding or changing a component, node, or service → update `docs/architecture.md`
- Adding or changing environment variables or config → update `docs/configuration.md`
- Adding or changing workflow behaviour, lifecycle states, or CLI flags → update `docs/workflows.md`
- Adding or changing a Goose recipe → update `docs/recipes.md`
- Adding or changing database schema, migrations, or state store API → update `docs/state-store.md`
- Adding or changing pre-commit hooks, test setup, or project structure → update `docs/development.md`

**When to update `README.md`:**

**After every change, ask: did I change any of the following?** If yes, update `README.md` before committing.

- The CLI command name, flags, or usage syntax → update the "Running Your First Workflow" section
- Installation steps (new tool, new `pip install`, new config step) → update the "Installation" section
- Prerequisites (new required tool or service) → update the "Prerequisites" list
- The component table (added/removed/renamed a top-level module) → update the "Components" table
- The high-level flow diagram (new stage, new participant) → update the ASCII diagram

Never add detailed usage, API reference, or troubleshooting to README — put it in `docs/`.

**When to update `docs/plan-recipe-flow.mmd`:**
- Adding new participants (components, services, databases) to the orchestration flow
- Changing the sequence of operations in the graph
- Adding or removing integration points (JIRA, SQLite, external APIs)
- Adding new workflow stages or steps
- Adding error handling or alternative paths
- Include the ticket ID in step annotations (e.g., "Step X: Description (AOS-XX)")

**Commit documentation with code**: Include documentation updates in the same commit/PR as the code changes they describe. Documentation commits should reference the ticket ID.

Example: `docs(AOS-39): Update plan-recipe-flow.mmd and workflows.md with WorkPlan posting workflow`

### Pull Request Process

1. **Before merging to main**: Always raise a Pull Request (PR)
   - Use the repository PR template (located in `.github/pull_request_template.md`)
   - Ensure all required information is filled out
   - Link the PR to the JIRA ticket

### After PR is Merged

Complete the following steps in order:

1. **Update JIRA ticket**: Transition the ticket to "Done" status
2. **Switch to main branch**: `git checkout main`
3. **Pull latest changes**: `git pull origin main`
4. **Delete the local feature branch**: `git branch -d feature/{jira_id}+{summary}`

## Quick Reference

```bash
# After agreeing on implementation plan:
# 1. Check if JIRA description already has the plan; if not, update it:
acli jira workitem view TICKET-ID  # inspect description
acli jira workitem edit --key "TICKET-ID" --description "<plan content>" -y

# 2. Create feature branch
git checkout -b feature/TICKET-ID+brief-summary

# 3. Assign ticket and transition to "In Progress"
acli jira workitem assign --key "TICKET-ID" --assignee "@me" -y
acli jira workitem transition --key "TICKET-ID" --status "In Progress" -y

# 5. Execute the implementation plan

# During implementation:
# - Update docs/plan-recipe-flow.mmd if workflow changes
# - Commit documentation with code changes

# After PR is merged:
acli jira workitem transition --key "TICKET-ID" --status "Done" -y
git checkout main
git pull origin main
git branch -d feature/TICKET-ID+brief-summary
```
