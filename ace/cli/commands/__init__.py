"""Command handler submodules for the ACE CLI.

Kept as a package (mirroring :mod:`dispatcher.commands`) so future verbs
(``items``, ``promote``, ``reject``, ``stats``, ``ontology``) can each land as
their own file without churning the entrypoint module.
"""
