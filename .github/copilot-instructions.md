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

When implementing code changes that affect the application workflow or orchestration:

1. **Update the flow diagram**: If your changes impact the orchestration flow, update `docs/plan-recipe-flow.mmd`
   - Add new participants (components, services, databases)
   - Add new steps or modify existing steps in the sequence
   - Update notes and annotations to reflect new behavior
   - Document error handling paths if added
   - Include the ticket ID in step annotations (e.g., "Step X: Description (AOS-XX)")

2. **Commit documentation with code**: Include documentation updates in the same PR
   - Documentation commits should reference the ticket ID
   - Example: `docs(AOS-39): Update plan-recipe-flow.mmd with WorkPlan posting workflow`

**When to update the flow diagram:**
- Adding new components or services to the workflow
- Changing the sequence of operations
- Adding or removing integration points (JIRA, SQLite, external APIs)
- Implementing new workflow stages or steps
- Adding error handling or alternative paths
- Changing data flow between components

### Pull Request Process

1. **Before merging to main**: Always raise a Pull Request (PR)
   - Use the repository PR template (located in `.github/pull_request_template.md`)
   - Ensure all required information is filled out
   - Link the PR to the JIRA ticket

### After PR is Merged

Complete the following steps in order:

1. **Update JIRA ticket**: Transition the ticket to "Closed" status
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
git checkout main
git pull origin main
git branch -d feature/TICKET-ID+brief-summary
```
