# Repository Workflow Instructions

Follow these rules when working in this repository.

## Feature Branch Workflow

### Before Starting Work on a Ticket

1. **Create a feature branch** using the naming convention: `feature/{jira_id}+{summary}`
   - Example: `feature/PROJ-123+add-authentication`
   - The branch name MUST include both the JIRA ticket ID and a brief summary
   
2. **Update the JIRA ticket**:
   - Assign the ticket to yourself
   - Transition the ticket status to "In Progress"

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
# Start work on a ticket
git checkout -b feature/PROJ-123+brief-summary

# After PR is merged
git checkout main
git pull origin main
git branch -d feature/PROJ-123+brief-summary
```
