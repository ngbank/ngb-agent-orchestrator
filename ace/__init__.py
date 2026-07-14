"""
ACE — Agentic Context Engine

Learns behavioral context items and ontology relationships from workflow
traces and injects them into planner / code generator prompts. See
docs/ACE/ace-implementation-plan.md for the rollout plan.

Boundary rule: ``orchestrator/`` may import from ``ace/``; ``ace/`` never
imports from ``orchestrator/`` graph code — it reads the workflow DB through
its own trace reader and shares only the ``state/`` migration/DB layer.
"""

__version__ = "0.1.0"
