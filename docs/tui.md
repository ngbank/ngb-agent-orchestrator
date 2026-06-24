# Dispatcher TUI

The Dispatcher TUI is an interactive, keyboard-driven interface for managing workflows built on top of the existing CLI commands. It provides a live-updating workflow list, detail pane, and action shortcuts — all without leaving the terminal.

---

## Installation

The TUI is included automatically when you install the project dependencies:

```bash
pip install -r requirements.txt
pip install -e .
```

`textual` is listed in `requirements.txt` and will be installed alongside the other runtime dependencies.

---

## Launch Commands

### From the dispatcher CLI

```bash
dispatcher --tui
```

### Standalone entry point

```bash
dispatcher-tui
```

Both commands launch the same Textual application.

---

## Keybindings

| Key | Action |
|-----|--------|
| `q` | Quit the TUI |
| `r` | Refresh the workflow list |
| `n` | Start a new workflow by entering a ticket key |
| `↑` / `↓` | Navigate the workflow list |
| `a` | Approve the selected pending WorkPlan |
| `j` | Reject the selected pending WorkPlan (prompts for reason) |
| `c` | Clarify the selected workflow (opens editor) |
| `y` | Retry the selected failed workflow |
| `x` | Cancel the selected active workflow (prompts for reason) |
| `o` | Approve the pending PR for the selected workflow |
| `p` | Comment on the pending PR for the selected workflow (opens editor) |
| `l` | Show logs for the selected workflow |
| `d` | Clear the entire database (confirmation dialog) |
| `space` | Pause / resume auto-scroll on the live log tail |

All actions delegate to the same handler functions used by the CLI, ensuring zero duplication of orchestration logic.

### Responsiveness

Service calls (start, approve, reject, retry, PR actions, clear-db) are
blocking — in local mode they drive the LangGraph workflow end to end, and in
remote mode they fire-and-forget then follow the SSE event stream. To keep the
UI responsive, every action that touches `WorkflowService` runs in a Textual
worker thread; the foreground loop stays free to refresh the workflow list,
tail logs, and accept input while the action is in flight. You'll see a
`{action}…` info notification when the work starts and a success/failure
notification when it finishes, after which the list refreshes automatically.

---

## Live Refresh

The workflow list refreshes automatically every 2 seconds by default. You can change the interval via the `DISPATCHER_TUI_POLL` environment variable (value in seconds):

```bash
DISPATCHER_TUI_POLL=5 dispatcher --tui
```

Set it to `0` to disable live refresh:

```bash
DISPATCHER_TUI_POLL=0 dispatcher --tui
```

---

## Live Log Tailing

When the selected workflow is `in_progress`, the detail pane shows a live tail
of captured stage logs (Goose `plan` and `execute` output) instead of the
static snapshot view. Lines are appended as they arrive and the view
auto-scrolls to the tail by default.

- **Trigger** — selecting any workflow whose status is `IN_PROGRESS`. Selecting
  a workflow in any other status (queued, paused for approval, completed,
  failed, cancelled) renders the regular static snapshot.
- **Stream end** — if the workflow transitions to a terminal state while the
  tail is open, the pane reverts to the snapshot view on the next refresh.
- **Pause** — press `space` to freeze auto-scroll while you inspect output;
  press `space` again to resume. New bytes still arrive in the background, so
  no log content is lost while paused.
- **Reconnect** — each poll passes the byte offset of the last received chunk
  via `WorkflowService.read_logs(after_offset=...)`, so transient transport
  errors (remote mode) recover without duplicating or losing lines.
- **Poll interval** — controlled by `DISPATCHER_TUI_TAIL_POLL` (seconds,
  default `1`). Set to `0` to disable the periodic tail; the initial backlog
  is still rendered when the workflow is selected.

In local dispatcher mode the tail reads bytes directly from the workflow's log
files. In remote mode it consumes the SSE log endpoint introduced in Stage B
(`GET /workflows/{id}/logs`) via `HttpWorkflowService.read_logs`, so the same
UX works against an orchestrator server without TUI-side changes.

---

## Screenshots

```
┌─────────────────────────────────────────────────────────────────────┐
│ Dispatcher TUI                                    Workflow Management │
├─────────────────────────────────────────────────────────────────────┤
│ Ticket       Status              Updated            Workflow ID     │
│ AOS-100      ⏸️ pending_approval 2024-01-01 01:00   wf-1            │
│ AOS-101      ❌ failed           2024-01-02 01:00   wf-2            │
│ AOS-102      🎉 completed        2024-01-03 01:00   wf-3            │
├─────────────────────────────────────────────────────────────────────┤
│ ⏸️ AOS-100 — pending_approval                                      │
│ Workflow ID: wf-1                                                   │
│ Status:     pending_approval                                        │
│ Created:    2024-01-01 00:00                                        │
│ Updated:    2024-01-01 01:00                                        │
│ Summary:    Add TUI support                                         │
├─────────────────────────────────────────────────────────────────────┤
│ q:quit  r:refresh  a:approve  j:reject  c:clarify  y:retry ...     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Architecture

The TUI is organised under `dispatcher/tui/`:

| File | Responsibility |
|------|----------------|
| `app.py` | Main `WorkflowTUI` App class, keybindings, refresh timer |
| `widgets.py` | `WorkflowList`, `DetailPane`, `StatusBar` |
| `modals.py` | `InputModal` (free-text) and `ConfirmModal` (yes/no) |
| `actions.py` | Thin wrappers that import and call existing CLI handlers |
| `screens.py` | Placeholder for future full-screen views |

The TUI reads workflow state exclusively through the `WorkflowService` Protocol
(`orchestrator.workflow_service.WorkflowService`). A single
`LocalWorkflowService` instance is built in `run_tui()` via
`build_local_workflow_service()` and passed into `WorkflowTUI(...)`; the same
service is forwarded to every action handler. The TUI does not import from
`state.workflow_repository` or read log files directly — those concerns live
behind the service boundary and can be swapped to a remote transport without
TUI changes.
