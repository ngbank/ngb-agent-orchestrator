# Cortex Plugin — Per-Service Chat Trigger (Option 3b)

A Cortex.io plugin that lets a developer paste a JIRA ticket id (or pick one from the service's linked tickets) and hold the **entire conversation** with the control plane inside the Cortex UI.

This is the **per-service** variant — one developer, one ticket, one repo. The fleet/fan-out variant (Option 3a) is documented separately.

## Why a chat UI in Cortex (not just a button)

A button that POSTs `start workflow for TICKET-XYZ` is a thin convenience over the Jira trigger. It doesn't justify the plugin work. The win is using Cortex as the **interaction surface** for the human-in-the-loop gates:

- Markdown, code blocks, syntax-highlighted diffs, side-by-side plan rendering.
- Multi-turn clarification without burying a 10-comment thread on a Jira ticket.
- Real-time updates (SSE/websocket) instead of webhook ping-pong.
- Single pane of glass: service catalog metadata, ownership, runbooks, recipes, and the live agent run all in one place.

Jira is **still the system of record**. Every meaningful event in the chat is mirrored to the ticket as a structured comment so the audit trail and the "PR link on the ticket" story remain intact.

## Scope

In scope:

- A Cortex plugin embedded on a service's catalog page.
- A chat panel that takes a ticket id as input and streams the workflow run.
- Approve / Reject / Clarify actions surfaced as buttons **and** as `/slash` commands typed into chat.
- Mirroring of decisions back to Jira as structured comments.

Out of scope (covered elsewhere):

- Fleet of changes across many repos → Option 3a.
- Standalone trigger without a service context → use the Jira trigger.
- Notifications when a gate has been waiting too long → MS Teams.

## End-to-end flow

```
┌──────────────────────────────────────────────────────────────────────┐
│ Cortex service page                                                  │
│                                                                      │
│  ┌─────────────────────────┐   ┌────────────────────────────────┐    │
│  │ Service metadata        │   │ Agent chat plugin              │    │
│  │ owner / stack / repo    │   │ ┌────────────────────────────┐ │    │
│  └─────────────────────────┘   │ │ Ticket: AOS-123    [Start] │ │    │
│                                │ ├────────────────────────────┤ │    │
│                                │ │ > Plan generated...        │ │    │
│                                │ │ [Approve] [Reject] [Clarify]│ │    │
│                                │ └────────────────────────────┘ │    │
│                                └─────────────┬──────────────────┘    │
└─────────────────────────────────────────────────┼──────────────────────┘
                                                  │ HTTPS + user identity
                                                  ▼
                                       ┌──────────────────────┐
                                       │ FleetOps Middleware  │
                                       │ (auth, rate limit,   │
                                       │  ticket lookup)      │
                                       └─────────────┬────────┘
                                                     │
                                                     ▼
                                       ┌──────────────────────┐
                                       │ Webhook Receiver     │
                                       │ (control plane)      │
                                       └─────────────┬────────┘
                                                     │
                                                     ▼
                                       ┌──────────────────────┐
                                       │ Workflow API /       │
                                       │ Orchestrator         │
                                       └─────────────┬────────┘
                                                     │
                            ┌────────────────────────┴────────────────────┐
                            ▼                                             ▼
                  Stream events to plugin                       Mirror digest
                  (SSE / websocket)                             comments to Jira
```

## Step-by-step

### 1. Start

1. Developer opens the service page in Cortex; the agent plugin is mounted in a side panel.
2. They paste/select a ticket id and click **Start** (or type `/start AOS-123`).
3. The plugin POSTs to the **FleetOps Middleware** with the user's Cortex identity token.
4. Middleware validates the user, checks they have permission for that service/ticket, and forwards a normalised `start_workflow` request to the control plane's Webhook Receiver.
5. Workflow API starts a run, opens an SSE stream back to the plugin.
6. Middleware posts a one-line digest comment on the Jira ticket: *"Run started from Cortex by @user — follow live at <link>."*

### 2. Plan + approval gate

1. Orchestrator generates the plan and pushes a `plan_ready` event to the SSE stream.
2. Plugin renders the plan with markdown + diff highlighting, plus `[Approve] [Reject] [Clarify]` buttons.
3. Developer clicks a button, or types `/approve`, `/reject <reason>`, `/clarify <question>`.
4. Plugin POSTs the structured action to the middleware → control plane.
5. Control plane resolves the gate, mirrors a digest comment to Jira:
   - `Plan approved by @user via Cortex — proceeding to execution.`
   - or `Plan rejected by @user via Cortex. Reason: <reason>.`

### 3. Clarification

The same chat thread handles back-and-forth clarification turns. Each turn is mirrored to Jira as a single combined comment when the gate closes (avoids comment-spam on the ticket).

### 4. Execution + done

1. Orchestrator streams `step_started`, `step_completed`, `tool_call`, `log` events.
2. Plugin renders these as a live activity feed.
3. On success, the plugin shows the PR link prominently and posts a final digest comment to Jira with PR link + summary + stats.

## Authoritative state

| Concern | Location |
|---|---|
| Workflow state | SQLite state store |
| Conversation transcript | Cortex chat (live) **+** Jira comments (digest) |
| Final artifacts (PR link) | Jira ticket |
| User identity for approvals | Cortex SSO token, validated by middleware |
| Trigger event | Jira webhook **or** Cortex plugin (both feed the Webhook Receiver) |

If Cortex is unavailable, the developer can fall back to the Jira trigger and slash-commands — no run is stranded.

## FleetOps Middleware

Sits between the developer portal and the control plane. Even for the per-service flow, the middleware is the right boundary because:

- Centralises auth (Cortex SSO → service-account token swap).
- Validates the request schema before it ever reaches the orchestrator.
- Rate-limits per user / per service.
- Translates portal-specific identifiers (Cortex service id, user id) into the control plane's domain (`ticket_id`, `repo`, `requester`).
- Will host the fleet (3a) endpoints later — same component, additional routes.
- Idempotency keys are generated here so plugin retries don't double-trigger.

## Plugin → middleware contract (sketch)

```http
POST /v1/runs
Authorization: Bearer <cortex-sso-token>
Idempotency-Key: <uuid>

{
  "ticket_id": "AOS-123",
  "service_id": "svc-payments-api",
  "source": "cortex-plugin"
}
```

```http
POST /v1/runs/{run_id}/gate
Authorization: Bearer <cortex-sso-token>
Idempotency-Key: <uuid>

{
  "gate_id": "<uuid>",
  "decision": "approve" | "reject" | "clarify",
  "payload": "<reason or answer>"
}
```

```http
GET /v1/runs/{run_id}/events     # SSE stream
```

## Mirroring rules to Jira

To keep the Jira ticket readable but complete:

- **Run start** → one digest comment.
- **Approval gate opened** → no Jira comment (visible in Cortex only).
- **Approval gate resolved** → one digest comment with decision + decider.
- **Clarification turn** → buffered; one consolidated comment when the gate closes.
- **Execution completed** → final digest comment with PR link, stats, and a Cortex deep-link to the full transcript.

## Notifications

The plugin shows live state, but a developer who isn't in Cortex needs a nudge. **MS Teams** posts a card when a gate has been waiting longer than a threshold (or immediately, depending on team config), linking back to either Cortex or Jira. Teams is notification-only — clicking the card opens the Cortex plugin or the Jira ticket; it does not accept gate decisions.

## Security

- Plugin only ever talks to the FleetOps Middleware; never directly to the orchestrator.
- Cortex SSO token validated on every request; signed identity is propagated to the audit log.
- The middleware enforces that the requester has access to both the Cortex service and the Jira ticket.
- All gate decisions are logged with `(user, source=cortex, run_id, gate_id, decision, ts)`.

## Open questions

- Should the plugin be installable per-team or globally? (Likely per-team for the pilot.)
- Threading model for clarification: single-thread or per-question subthreads?
- Do we render execution diffs live or only on completion? (Start with on-completion to keep payload small.)
- How to surface previous runs against the same ticket without polluting the chat (likely a "history" tab in the plugin).
