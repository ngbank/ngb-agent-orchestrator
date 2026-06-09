"""Observable SQLite checkpointer wrapping LangGraph's SqliteSaver.

Intercepts every checkpoint ``put`` call to emit an OTel span recording the
state transition.  This gives a low-level audit trail (every checkpoint write)
complementing the higher-level node spans from the stream interceptor.

Usage (in graph/builder.py):

    from state.observable_sqlite_saver import ObservableSqliteSaver

    conn = sqlite3.connect(get_db_path(), check_same_thread=False)
    checkpointer = ObservableSqliteSaver(conn)
    graph = builder.compile(checkpointer=checkpointer)
"""

from __future__ import annotations

import sqlite3
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from graph.otel.context import OtelContext


class ObservableSqliteSaver(SqliteSaver):
    """SqliteSaver subclass that records each checkpoint as an OTel span.

    All LangGraph checkpointing behaviour is preserved unchanged.  The only
    addition is an OTel span wrapping the ``put`` call, carrying the current
    workflow correlation attributes from context variables.

    Args:
        conn: Open ``sqlite3.Connection``.  The connection is passed directly
              to ``SqliteSaver.__init__`` without modification.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__(conn)

    def put(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        """Write checkpoint and emit a ``graph.checkpoint`` OTel span."""
        tracer = trace.get_tracer("graph.orchestrator")
        ctx = OtelContext.capture()

        channel_versions = checkpoint.get("channel_versions", {})
        step = metadata.get("step", -1)

        attributes = {
            **ctx.as_attributes(),
            "checkpoint.step": step,
            "checkpoint.channel_count": len(channel_versions),
        }

        thread_id = config.get("configurable", {}).get("thread_id")
        if thread_id:
            attributes["graph.thread_id"] = str(thread_id)

        with tracer.start_as_current_span("graph.checkpoint", attributes=attributes) as span:
            try:
                result = super().put(config, checkpoint, metadata, new_versions)
                span.set_status(Status(StatusCode.OK))
                return result
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
