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

The function returns a :class:`RenderedContextBlock` carrying the rendered
markdown plus the metadata callers need to persist an injection event: the
list of retrieved item IDs, the durable block cache key (when the synthesizer
produced one), and the rendering mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ace.config import confidence_to_tier, get_ace_settings
from ace.models import ContextItem
from ace.retrieval.retrieve import retrieve_context_items
from ace.retrieval.synthesizer import SynthesizedBlock, TicketContext, synthesize_context_block

# Rendering modes recorded on ``ace_injection_events.synthesizer``.
MODE_SYNTHESIZER = "synthesizer"
MODE_FLAT_LIST = "flat_list"
MODE_EMPTY = "empty"


@dataclass
class RenderedContextBlock:
    """Rendered context block plus metadata for injection-event persistence.

    ``markdown`` is the string passed to Goose; ``item_ids`` are the retrieved
    ``ContextItem`` identifiers that shaped the block; ``block_cache_key`` is
    the durable ``synthesized_context_blocks.cache_key`` when the synthesizer
    ran (``None`` for flat-list / empty renderings); ``mode`` records which
    rendering path produced the block.
    """

    markdown: str = ""
    item_ids: list[str] = field(default_factory=list)
    block_cache_key: Optional[str] = None
    mode: str = MODE_EMPTY

    def is_empty(self) -> bool:
        return not self.markdown.strip()


def render_context_block(
    ticket_context: TicketContext,
    *,
    task_type: Optional[str] = None,
    file_path: Optional[str] = None,
    query_text: str = "",
    top_k: Optional[int] = None,
) -> RenderedContextBlock:
    """Retrieve context items and render them for injection into a prompt.

    Retrieval uses *ticket_context* for applicability filtering (project, repo,
    platform) and the caller-supplied *query_text* / *task_type* / *file_path*
    for keyword ranking and scope filtering.

    Returns an empty :class:`RenderedContextBlock` when no items are available.
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
        return RenderedContextBlock()

    item_ids = [item.id for item in items]

    if settings.is_synthesizer_active():
        block: SynthesizedBlock = synthesize_context_block(items, ticket_context)
        return RenderedContextBlock(
            markdown=block.to_markdown(),
            item_ids=item_ids,
            block_cache_key=block.cache_key,
            mode=MODE_SYNTHESIZER,
        )

    return RenderedContextBlock(
        markdown=_flat_list_format(items),
        item_ids=item_ids,
        block_cache_key=None,
        mode=MODE_FLAT_LIST,
    )


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


__all__ = [
    "RenderedContextBlock",
    "MODE_SYNTHESIZER",
    "MODE_FLAT_LIST",
    "MODE_EMPTY",
    "render_context_block",
]
