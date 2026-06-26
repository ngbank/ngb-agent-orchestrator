"""Tests for the ``mark_executing`` entry node of the code_generator subgraph.

The node's job is to flip the workflow row to ``IN_PROGRESS`` so the live
status display (CLI list / TUI) reflects that ``execute_plan`` is actually
running, instead of staying stuck at the prior ``APPROVED`` or
``PR_COMMENTED`` status for the duration of clone + Goose + push + PR.
"""

from unittest.mock import patch

from orchestrator.code_generator.nodes.mark_executing import mark_executing
from state.workflow_status import WorkflowStatus


def test_mark_executing_flips_to_in_progress():
    state = {"workflow_id": "wf-abc"}
    with patch("orchestrator.code_generator.nodes.mark_executing.update_status") as mock_status:
        result = mark_executing(state)

    mock_status.assert_called_once_with(
        "wf-abc",
        WorkflowStatus.IN_PROGRESS,
        actor="execute_plan",
        reason="execute_plan started",
    )
    assert result == {}


def test_mark_executing_noop_without_workflow_id():
    # Defensive: graph topology never invokes this node without a workflow_id,
    # but if state is malformed we should not call update_status with None.
    with patch("orchestrator.code_generator.nodes.mark_executing.update_status") as mock_status:
        result = mark_executing({})

    mock_status.assert_not_called()
    assert result == {}


def test_code_generator_entry_point_is_mark_executing():
    """Verify the subgraph wires ``mark_executing`` before ``repo_setup``.

    Guards against accidental reverts that would leave the status stuck at
    ``APPROVED`` during execute_plan.
    """
    from orchestrator.code_generator.builder import build_code_generator

    graph = build_code_generator()
    # LangGraph exposes the compiled graph structure; the entry node is
    # reachable via the underlying graph nodes mapping.
    assert "mark_executing" in graph.get_graph().nodes
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("mark_executing", "repo_setup") in edges
    assert ("__start__", "mark_executing") in edges
