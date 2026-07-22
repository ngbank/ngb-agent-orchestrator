"""Shared retrieval and temporary-file helpers for workflow context items."""

import logging
import os
import tempfile

from ace.retrieval import render_context_block
from ace.retrieval.synthesizer import TicketContext

logger = logging.getLogger(__name__)


def retrieve_context_items(
    ticket_key: str,
    ticket_summary: str,
    recipe_target: str,
    query_text: str,
    top_k: int,
) -> str:
    """Render applicable context items without blocking the calling workflow."""
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
        return ""


def write_context_items_file(ticket_key: str, block: str) -> str | None:
    """Materialize a non-empty context block for a Goose recipe invocation."""
    if not block.strip():
        return None

    fd, path = tempfile.mkstemp(suffix="_context_items.md", prefix=f"{ticket_key}_")
    os.close(fd)
    with open(path, "w") as context_file:
        context_file.write(block)
    return path
