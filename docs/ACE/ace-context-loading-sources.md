# ACE Context Loading Sources

Use this file at the start of any new session to quickly restore the canonical context for this project.

## Primary project note

- `0 Designing a Context Engineering Framework.md`

## ACE paper and core web docs

- ACE paper (HTML): https://arxiv.org/html/2510.04618v3
- ACE paper (abstract): https://arxiv.org/abs/2510.04618
- LangGraph docs: https://langchain-ai.github.io/langgraph/

## ACE implementation repositories

- SDK-oriented implementation: https://github.com/kayba-ai/agentic-context-engine
- Reference/paper-style implementation: https://github.com/ace-agent/ace

## Current orchestrator repositories

- Orchestrator (git remote): `git@github.com-ngbank:ngbank/ngb-agent-orchestrator.git`
- Orchestrator (local): `/Users/romulo/Projects/ngb-agent-orchestrator`

## Local orchestrator docs to load first

- `/Users/romulo/Projects/ngb-agent-orchestrator/README.md`
- `/Users/romulo/Projects/ngb-agent-orchestrator/docs/architecture.md`
- `/Users/romulo/Projects/ngb-agent-orchestrator/docs/workflows.md`
- `/Users/romulo/Projects/ngb-agent-orchestrator/docs/recipes.md`
- `/Users/romulo/Projects/ngb-agent-orchestrator/docs/state-store.md`

## Local orchestrator code anchors to load first

- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/builder.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/builder.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/nodes/generate_plan.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/nodes/await_workplan_clarification.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/nodes/run_goose.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/state/sqlite_state_store.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/recipes/plan.yaml`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/recipes/generate_code.yaml`

## PR-stage feedback-loop sources

- GitHub pull request reviews API: https://docs.github.com/en/rest/pulls/reviews
- GitHub pull request review comments API: https://docs.github.com/rest/pulls/comments
- GitHub issue comments API (PR-level comments): https://docs.github.com/rest/issues/comments
- GitHub webhook events and payloads: https://docs.github.com/en/webhooks/webhook-events-and-payloads
- Local PR output anchor (`pr_url` persistence): `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/nodes/push_and_create_pr.py`
- Local code generation summary formatter (`pr_url` rendering): `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/utilities/formatter.py`

## Learning doc load order for new sessions

1. `00-ace-primer-roadmap.md`
2. `ace-context-loading-sources.md`
3. Latest covered topic docs (numbered `NN-ace-*.md`)
4. First not-started topic from roadmap curriculum
