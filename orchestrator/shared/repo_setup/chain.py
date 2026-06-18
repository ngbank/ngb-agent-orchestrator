"""Helpers for wiring shared repo-setup chains into subgraph builders."""

from typing import Callable


def add_repo_setup_chain(
    builder,
    *,
    route_after_resolve: Callable,
    route_after_fetch_token: Callable,
    route_after_clone: Callable,
    on_resolve_success: str,
    on_resolve_failure: str,
    on_fetch_success: str,
    on_fetch_failure: str,
    on_clone_success: str,
    on_clone_failure: str,
) -> None:
    """Attach standard conditional edges for resolve/token/clone nodes."""
    builder.add_conditional_edges(
        "resolve_repo",
        route_after_resolve,
        {
            on_resolve_success: on_resolve_success,
            on_resolve_failure: on_resolve_failure,
        },
    )
    builder.add_conditional_edges(
        "fetch_github_token",
        route_after_fetch_token,
        {
            on_fetch_success: on_fetch_success,
            on_fetch_failure: on_fetch_failure,
        },
    )
    builder.add_conditional_edges(
        "clone_repo",
        route_after_clone,
        {
            on_clone_success: on_clone_success,
            on_clone_failure: on_clone_failure,
        },
    )
