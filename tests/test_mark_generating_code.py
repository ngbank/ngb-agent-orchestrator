"""Tests for the ``mark_generating_code`` entry node of the code_generator subgraph.

The node's job is to flip the workflow row to ``IN_PROGRESS`` so the live
status display (CLI list / TUI) reflects that ``generate_code`` is actually
running, instead of staying stuck at the prior ``APPROVED`` or
``PR_COMMENTED`` status for the duration of clone + Goose + push + PR.
"""

from unittest.mock import patch

from orchestrator.code_generator.nodes.mark_generating_code import mark_generating_code
from state.workflow_status import WorkflowStatus


def test_mark_generating_code_flips_to_in_progress():
    state = {"workflow_id": "wf-abc"}
    with patch(
        "orchestrator.code_generator.nodes.mark_generating_code.update_status"
    ) as mock_status:
        result = mark_generating_code(state)

    mock_status.assert_called_once_with(
        "wf-abc",
        WorkflowStatus.IN_PROGRESS,
        actor="generate_code",
        reason="generate_code started",
    )
    assert result == {}


def test_mark_generating_code_noop_without_workflow_id():
    # Defensive: graph topology never invokes this node without a workflow_id,
    # but if state is malformed we should not call update_status with None.
    with patch(
        "orchestrator.code_generator.nodes.mark_generating_code.update_status"
    ) as mock_status:
        result = mark_generating_code({})

    mock_status.assert_not_called()
    assert result == {}


def test_code_generator_entry_point_is_mark_generating_code():
    """Verify the subgraph wires ``mark_generating_code`` before ``repo_setup``.

    Guards against accidental reverts that would leave the status stuck at
    ``APPROVED`` during generate_code.
    """
    from orchestrator.code_generator.builder import build_code_generator

    graph = build_code_generator()
    # LangGraph exposes the compiled graph structure; the entry node is
    # reachable via the underlying graph nodes mapping.
    assert "mark_generating_code" in graph.get_graph().nodes
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("mark_generating_code", "repo_setup") in edges
    assert ("__start__", "mark_generating_code") in edges
