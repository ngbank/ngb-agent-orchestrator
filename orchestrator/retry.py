"""Workflow retry helpers.

Provides functions to resume a failed LangGraph workflow from the node that
failed, by rewinding the checkpointer state to the snapshot immediately
before that node was due to run, then re-invoking the graph.

The work_planner subgraph is compiled without its own checkpointer, so any
failure inside it (validate_input, check_duplicate, fetch_ticket, generate_plan,
validate_plan, store_plan, post_to_jira) is rewound to the top-level
``work_planner`` node.  A ``generate_code`` failure is rewound to the
``generate_code`` node directly.
"""

from typing import Optional

from langchain_core.runnables import RunnableConfig

WORK_PLANNER_NODES = {
    "validate_input",
    "check_duplicate",
    "fetch_ticket",
    "create_workflow_record",
    "generate_plan",
    "validate_plan",
    "await_workplan_clarification",
    "store_plan",
    "post_to_jira",
}


def resolve_parent_node(failed_node: str) -> str:
    """Map a failed-node name to the top-level orchestrator node to rewind to.

    Work_planner subgraph nodes collapse to ``"work_planner"`` because the
    subgraph runs atomically inside the parent graph.  All other nodes map
    to themselves.
    """
    if failed_node in WORK_PLANNER_NODES:
        return "work_planner"
    return failed_node


def find_rewind_config(
    graph, thread_config: RunnableConfig, parent_node: str
) -> Optional[RunnableConfig]:
    """Walk checkpoint history to find the snapshot where ``parent_node`` was next.

    Returns the LangGraph ``config`` (with checkpoint_id) for the snapshot
    immediately before ``parent_node`` ran, or ``None`` if no such snapshot
    exists.  The most recent matching snapshot wins (covers cases where the
    same node ran multiple times, e.g. clarification loops).
    """
    for snapshot in graph.get_state_history(thread_config):
        if parent_node in (snapshot.next or ()):
            return snapshot.config
    return None


def prepare_retry(graph, thread_config: RunnableConfig, failed_node: str) -> RunnableConfig:
    """Rewind graph state so a re-invocation will re-run ``failed_node``.

    Clears ``error`` and ``failed_node`` from the checkpointed state at the
    rewind point.  Returns the updated config that callers should pass to
    ``graph.invoke(None, config=...)``.

    Raises:
        ValueError: when no checkpoint exists for the resolved parent node.
    """
    parent_node = resolve_parent_node(failed_node)
    rewind_config = find_rewind_config(graph, thread_config, parent_node)
    if rewind_config is None:
        raise ValueError(
            f"No checkpoint found before node '{parent_node}' "
            f"(failed_node='{failed_node}'); cannot retry."
        )
    graph.update_state(rewind_config, {"error": None, "failed_node": None})
    return thread_config
