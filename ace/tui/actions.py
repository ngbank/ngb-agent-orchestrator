"""Action handlers for the ACE TUI.

For ticket 3.4 (app scaffold + staging queue screen), no service-mutating
actions are registered — promote / reject / edit-scope land in ticket 3.5.
This module exists so ``ace.tui`` mirrors the full module structure of
``dispatcher/tui`` and the handlers for 3.5 have a natural home.
"""

from __future__ import annotations


class ActionError(Exception):
    """Raised when a TUI action fails; surfaced as an error notification."""

    pass
