"""Context-item retrieval: ``retrieve_context_items()`` and ``render_context_block()``.

``render_context_block`` is the single entry point for injection points
(planner, code generator, PR re-run).  It composes retrieval → synthesis →
serialisation into one call, respecting the ``ace_synthesizer_enabled`` flag:

- When the synthesizer is **on**, retrieved items are fed through
  ``synthesize_context_block()`` and the structured markdown document is
  returned.
- When the synthesizer is **off**, items are rendered as a legacy flat list
  (tier-labelled bullets) so that callers can be wired up before the
  synthesizer is production-ready.
"""

from __future__ import annotations

from typing import Optional

from ace.config import confidence_to_tier, get_ace_settings
from ace.models import ContextItem
from ace.retrieval.retrieve import retrieve_context_items
from ace.retrieval.synthesizer import SynthesizedBlock, TicketContext, synthesize_context_block


def render_context_block(
    ticket_context: TicketContext,
    *,
    task_type: Optional[str] = None,
    file_path: Optional[str] = None,
    query_text: str = "",
    top_k: Optional[int] = None,
) -> str:
    """Retrieve context items and render them for injection into a prompt.

    Retrieval uses *ticket_context* for applicability filtering (project, repo,
    platform) and the caller-supplied *query_text* / *task_type* / *file_path*
    for keyword ranking and scope filtering.

    Returns an empty string when no items are available.

    Parameters
    ----------
    ticket_context:
        Describes the current task.  Used for applicability dimensions and as
        the synthesizer context.
    task_type:
        Optional scope filter — matches items scoped to this task type.
    file_path:
        Optional scope filter — matches items whose ``scope_value`` glob pattern
        matches this path.
    query_text:
        Text used for keyword ranking against item descriptions.
    top_k:
        Override for the maximum number of items.  Defaults to the value from
        ``ACESettings.top_k``.
    """
    settings = get_ace_settings()
    effective_top_k = top_k if top_k is not None else settings.top_k

    items = retrieve_context_items(
        task_type=task_type,
        file_path=file_path,
        query_text=query_text,
        top_k=effective_top_k,
        project=ticket_context.project,
        repo=ticket_context.repo,
        platform=ticket_context.platform,
    )

    if not items:
        return ""

    if settings.is_synthesizer_active():
        block: SynthesizedBlock = synthesize_context_block(items, ticket_context)
        return block.to_markdown()

    return _flat_list_format(items)


def _flat_list_format(items: list[ContextItem]) -> str:
    """Legacy flat-list format — used when the synthesizer is off.

    Each item is rendered as a tier-labelled bullet.  This is the format that
    AOS-235 through AOS-238 originally assumed before the synthesizer was
    introduced.
    """
    lines: list[str] = []
    for item in items:
        tier = confidence_to_tier(item.confidence) or "TENTATIVE"
        conflicts_note = ""
        if item.conflicts_with:
            conflicts_note = f" ⚠ conflicts with: {', '.join(item.conflicts_with)}"
        lines.append(f"- [{tier}] {item.description}{conflicts_note}")
    return "\n".join(lines)
