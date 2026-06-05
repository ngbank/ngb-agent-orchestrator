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

All actions delegate to the same handler functions used by the CLI, ensuring zero duplication of orchestration logic.

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

The TUI reads workflow state directly from the SQLite store (`state.workflow_repository.list_workflows`) and delegates all mutating actions to the handler functions in `dispatcher/commands/`.
