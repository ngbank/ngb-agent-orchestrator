# Jira Trigger

Jira is the **system of record** for every workflow run. Even when a run is started or interacted with through another channel (Cortex plugin, FleetOps), the authoritative state, audit trail, and final artifacts (plan summary, PR link) live on the Jira ticket.

## Goals

- Single, low-overhead way to start an agentic workflow against a ticket.
- Human-in-the-loop gates (approval, clarification) handled through the same ticket without overloading Jira's status field.
- Idempotent against duplicate webhook deliveries and accidental status flips.

## Statuses

Only **two** custom statuses are used to drive the loop:

| Status | Meaning |
|---|---|
| `Ready for agent` | Trigger edge — control plane should pick the ticket up and resume/advance the workflow. |
| `Awaiting human review` | The control plane is parked on a gate (approval or clarification) and is waiting for the human. |

All fine-grained workflow state (`AwaitingApproval`, `WorkPlanClarification`, `Executing`, `Failed`, etc.) is tracked in the local SQLite state store — **not** in Jira.

## End-to-end flow

```
┌─────────┐  status: Ready for agent       ┌────────────────────┐
│  Human  │ ─────────────────────────────▶ │  Jira              │
└─────────┘                                └─────────┬──────────┘
                                                     │ webhook
                                                     ▼
                                          ┌────────────────────┐
                                          │ Webhook Receiver   │
                                          │ (control plane)    │
                                          └─────────┬──────────┘
                                                    │ resolve gate
                                                    ▼
                                          ┌────────────────────┐
                                          │ Workflow API /     │
                                          │ Orchestrator       │
                                          └─────────┬──────────┘
                                                    │
                ┌───────────────────────────────────┤
                ▼                                   ▼
       Post bot comment with                Status → Awaiting
       plan + slash-commands                  human review
                                                    │
                                                    ▼
                                              (human reads,
                                               replies, flips
                                               status back)
```

## Step-by-step

### 1. Start

1. Human transitions a ticket to `Ready for agent`.
2. Jira sends a webhook to the control plane.
3. **Webhook Receiver** validates the signature, dedupes by webhook delivery id, and enqueues a `start_workflow` command keyed by `ticket_id`.
4. Workflow API starts a new run, persists `(ticket_id, run_id)` in the state store.

### 2. Plan + approval gate

1. Orchestrator runs the plan recipe and posts a **single bot comment** to the ticket with:
   - The work plan summary.
   - An explicit instruction line:

     ```
     Reply with a comment starting with one of:
       /approve
       /reject <reason>
       /clarify <answer>
     Then move the ticket back to "Ready for agent".
     ```

   - A hidden marker (`<!-- gate_id: <uuid> -->`) at the end of the comment so the receiver can locate the gate boundary later.
2. Jira status → `Awaiting human review`.
3. State store records: `gate_id`, `gate_type=approval`, `bot_comment_id`, `awaiting_since`.

### 3. Human responds

1. Human adds a comment beginning with `/approve`, `/reject ...` or `/clarify ...`.
2. Human flips status back to `Ready for agent`.
3. Jira fires the webhook again.

### 4. Resume

The Webhook Receiver:

1. Looks up the **active gate** for `ticket_id` in the state store.
2. If no active gate exists → treat as a fresh start (idempotent re-run guard checks the run's terminal state first).
3. If a gate exists, read comments **newer than `bot_comment_id`** authored by humans (skip the bot account).
4. Find the **most recent** comment that matches a known command regex.
5. Branch:
   - **Match found** → resolve the gate with that decision, store `resolving_comment_id`, resume the graph.
   - **No match** → post a bot comment:

     ```
     I couldn't find a /approve, /reject, or /clarify command in your latest
     comment. Please reply with one of those commands and move the ticket back
     to "Ready for agent".
     ```

     Status → `Awaiting human review`. No state change.

### 5. Clarification (same loop, different commands)

The clarification gate behaves identically. The bot comment lists the question(s) and the only allowed command is `/clarify <answer>`. After resuming, the orchestrator may post **another** clarification gate or move on to approval — the loop is reused.

### 6. Done

After execution succeeds the orchestrator:

1. Posts a final bot comment with the PR link, summary, and execution stats.
2. Transitions the ticket to a terminal status (e.g. `Done` or back to whatever the team's workflow uses post-merge — out of scope here).

## Slash-command grammar

| Command | Allowed in | Payload | Notes |
|---|---|---|---|
| `/approve` | Approval gate | none | Anything after the keyword on the same line is ignored. |
| `/reject <reason>` | Approval gate | required free text | Reason is stored verbatim and surfaced in metrics. |
| `/clarify <answer>` | Clarification gate | required free text | Multi-line answers allowed; everything after the keyword until end-of-comment is the payload. |
| `/retry` | Any gate after a failure | optional reason | Re-runs from the last checkpoint. |
| `/cancel` | Any gate | optional reason | Marks the run cancelled. |

Commands are parsed with a strict regex anchored to the first non-whitespace token of the comment. Quoted replies and bot comments are skipped.

## Idempotency rules

Every external event must be safe to replay:

- **Webhook delivery** — dedupe on the Jira webhook delivery id (header). Store last N processed ids per ticket.
- **Gate resolution** — keyed on `(ticket_id, gate_id, resolving_comment_id)`. A repeat of the same comment id is a no-op.
- **Bot comments** — written through an "exactly-one-per-gate" guard: if a bot comment with the current `gate_id` marker already exists, do not post another.

## Edge cases

| Case | Behaviour |
|---|---|
| Status flipped to `Ready for agent` with no comment | Bot replies asking for a command, status → `Awaiting human review`. |
| Two humans comment with conflicting commands | The **most recent** human comment wins. |
| Human edits an old comment to add a command | Ignored — only comments authored after `bot_comment_id` are considered. |
| Webhook lost (Jira blip) | Reconciliation poller scans tickets in `Ready for agent` older than N minutes and re-fires. |
| Workflow fails mid-execution | Bot posts failure summary, status → `Awaiting human review`, allowed commands = `/retry`, `/cancel`. |
| Ticket deleted / moved | Run is marked `cancelled` on the next reconciliation pass. |

## What lives where

| Concern | Location |
|---|---|
| Authoritative workflow state | SQLite state store (`state/state_store.py`) |
| Human conversation transcript | Jira comments |
| Trigger | Jira status change → webhook |
| Audit trail | Jira ticket (comments + history) + state store |
| Notifications ("you have a gate waiting") | MS Teams (out of band, read-only link to the ticket) |

## Security

- Validate the Jira webhook signature on every delivery.
- Restrict slash-commands to users with the appropriate Jira role (configurable per project).
- Bot comments are posted by a dedicated service account; the receiver always filters this account out when scanning for human commands.
- Secrets (Jira API token, webhook signing key) live in Key Vault.

## Open questions / future work

- Do we need a `/edit-plan` command for inline plan tweaks, or is reject-and-replan good enough? (Default: reject-and-replan.)
- Per-team customisation of the two status names (mapping table in `config/`).
- Reconciliation poller cadence and ownership.
