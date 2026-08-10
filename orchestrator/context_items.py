"""Shared retrieval and temporary-file helpers for workflow context items."""

import logging
import os
import tempfile

from ace.retrieval import RenderedContextBlock, render_context_block
from ace.retrieval.synthesizer import TicketContext
from ace.telemetry import record_injection_event
from otel.instrumentation import emit_ace_injection

logger = logging.getLogger(__name__)


def retrieve_context_items(
    ticket_key: str,
    ticket_summary: str,
    recipe_target: str,
    query_text: str,
    top_k: int,
) -> RenderedContextBlock:
    """Render applicable context items without blocking the calling workflow.

    Returns an empty :class:`RenderedContextBlock` on retrieval failure so
    callers can continue without ACE context.
    """
    project = ticket_key.split("-", 1)[0] if "-" in ticket_key else ticket_key
    ticket_context = TicketContext(
        ticket_key=ticket_key,
        ticket_summary=ticket_summary,
        project=project,
        recipe_target=recipe_target,
    )
    try:
        return render_context_block(ticket_context, query_text=query_text, top_k=top_k)
    except Exception:  # noqa: BLE001 — retrieval must not block workflow execution
        logger.warning(
            "ACE context retrieval failed for %s — proceeding without context items",
            ticket_key,
            exc_info=True,
        )
        return RenderedContextBlock()


def write_context_items_file(
    ticket_key: str,
    rendered: RenderedContextBlock,
    *,
    workflow_id: str | None = None,
    injection_point: str | None = None,
) -> str | None:
    """Record and materialize a non-empty context block for a Goose invocation.

    When *workflow_id* and *injection_point* are both present, an injection
    event is recorded and an ``ace.injection`` OTel span is emitted before the
    temp file is written. Persistence failures are logged and never raise.
    """
    if rendered.is_empty():
        return None

    if workflow_id and injection_point:
        record_injection_event(
            workflow_id=workflow_id,
            ticket_key=ticket_key,
            injection_point=injection_point,
            synthesizer=rendered.mode,
            block_cache_key=rendered.block_cache_key,
            retrieved_item_ids=rendered.item_ids,
            rendered_length=len(rendered.markdown),
        )
        emit_ace_injection(
            workflow_id=workflow_id,
            injection_point=injection_point,
            synthesizer=rendered.mode,
            rendered_length=len(rendered.markdown),
            block_cache_key=rendered.block_cache_key,
            item_ids=rendered.item_ids,
        )

    fd, path = tempfile.mkstemp(suffix="_context_items.md", prefix=f"{ticket_key}_")
    os.close(fd)
    with open(path, "w") as context_file:
        context_file.write(rendered.markdown)
    return path
