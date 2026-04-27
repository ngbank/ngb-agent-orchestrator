# Repository Workflow Instructions

Follow these rules when working in this repository.

## Feature Branch Workflow

### Implementation Plan Workflow

When creating an implementation plan for a ticket:

1. **Create the plan**: Generate a detailed implementation plan in a temporary file (e.g., `{TICKET_ID}-implementation-plan.md`)
2. **Review and agree**: Discuss and refine the plan with the user
3. **Once the plan is agreed upon**, follow the steps below to start work

### Before Starting Work on a Ticket

After agreeing on an implementation plan, complete these steps in order:

1. **Update the JIRA ticket description** with the implementation plan content
   ```bash
   acli jira workitem edit --key "TICKET-ID" --description "$(cat TICKET-ID-implementation-plan.md)" -y
   ```

2. **Delete the temporary implementation plan file** from the project
   ```bash
   rm TICKET-ID-implementation-plan.md
   ```

3. **Create a feature branch** using the naming convention: `feature/{jira_id}+{summary}`
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
# 1. Update ticket description
acli jira workitem edit --key "TICKET-ID" --description "$(cat TICKET-ID-implementation-plan.md)" -y

# 2. Delete temporary plan file
rm TICKET-ID-implementation-plan.md

# 3. Create feature branch
git checkout -b feature/TICKET-ID+brief-summary

# 4. Assign ticket and transition to "In Progress"
acli jira workitem assign --key "TICKET-ID" --assignee "@me" -y
acli jira workitem transition --key "TICKET-ID" --status "In Progress" -y

# 5. Execute the implementation plan

# After PR is merged:
git checkout main
git pull origin main
git branch -d feature/TICKET-ID+brief-summary
```
