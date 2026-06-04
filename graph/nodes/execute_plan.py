"""Node: execute_plan — compatibility shim.

The execute_plan logic has been decomposed into the executor subgraph under
graph/executor/.  This module is retained only to avoid breaking any external
imports of _failure_summary during the transition.
"""

from graph.executor.nodes.resolve_repo import _failure_summary  # noqa: F401
