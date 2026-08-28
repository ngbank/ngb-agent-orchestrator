"""Unit tests for the ``run_learning_pipeline`` tail node.

The node is invoked on every terminal path of the orchestrator graph. It must:
- delegate to ``ace.pipeline.runner.run_mining`` with the current workflow_id,
- swallow any exception raised by the pipeline (never affect terminal status),
- no-op when there is no workflow_id in state.
"""

from unittest.mock import patch

from ace.pipeline.runner import RunnerResult
from orchestrator.nodes.run_learning_pipeline import run_learning_pipeline


def test_run_learning_pipeline_invokes_run_mining_with_workflow_id():
    state = {"workflow_id": "wf-123"}

    fake_result = RunnerResult(processed=1, succeeded=1)
    with patch("ace.pipeline.runner.run_mining", return_value=fake_result) as mock_run_mining:
        out = run_learning_pipeline(state)

    mock_run_mining.assert_called_once_with(workflow_id="wf-123")
    assert out == {}


def test_run_learning_pipeline_swallows_exceptions():
    """A pipeline failure must never propagate — the terminal status is sacred."""
    state = {"workflow_id": "wf-456"}

    with patch(
        "ace.pipeline.runner.run_mining", side_effect=RuntimeError("boom")
    ) as mock_run_mining:
        out = run_learning_pipeline(state)

    mock_run_mining.assert_called_once_with(workflow_id="wf-456")
    assert out == {}


def test_run_learning_pipeline_noop_without_workflow_id():
    """Fresh runs that failed before persistence have no workflow_id."""
    with patch("ace.pipeline.runner.run_mining") as mock_run_mining:
        out = run_learning_pipeline({})

    mock_run_mining.assert_not_called()
    assert out == {}


def test_run_learning_pipeline_noop_when_workflow_id_none():
    with patch("ace.pipeline.runner.run_mining") as mock_run_mining:
        out = run_learning_pipeline({"workflow_id": None})

    mock_run_mining.assert_not_called()
    assert out == {}


# ---------------------------------------------------------------------------
# Compiled-graph integrity: every terminal edge routes through the tail node
# so mining runs on approved / rejected / failed workflows.
# ---------------------------------------------------------------------------


def test_compiled_graph_registers_run_learning_pipeline_node(tmp_path):
    """The tail node must be present in the compiled orchestrator graph."""
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    from orchestrator.builder import build_orchestrator

    conn = sqlite3.connect(tmp_path / "checkpoint.db", check_same_thread=False)
    graph = build_orchestrator(checkpointer=SqliteSaver(conn))

    assert "run_learning_pipeline" in graph.nodes


def test_compiled_graph_terminal_edges_pass_through_tail(tmp_path):
    """Every non-loop parent must be able to reach ``run_learning_pipeline``.

    LangGraph resolves conditional edges at runtime, but the destination map
    passed to ``add_conditional_edges`` is inspectable on the raw builder.
    We rebuild the graph structure and assert that each parent's destination
    map includes the tail node — the compile-time contract that guarantees
    mining runs on every terminal path.
    """
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    from orchestrator.builder import build_orchestrator

    conn = sqlite3.connect(tmp_path / "checkpoint.db", check_same_thread=False)
    graph = build_orchestrator(checkpointer=SqliteSaver(conn))

    # ``graph.builder.branches`` maps parent-node -> {branch_name -> Branch}
    # and each Branch.ends is the destination map from ``add_conditional_edges``.
    branches = graph.builder.branches
    for parent in ("work_planner", "await_approval", "generate_code", "await_pr_approval"):
        assert parent in branches, f"{parent} has no conditional edges"
        destinations = set()
        for branch in branches[parent].values():
            destinations.update(branch.ends.values())
        assert (
            "run_learning_pipeline" in destinations
        ), f"{parent} does not route to run_learning_pipeline on any terminal path"
