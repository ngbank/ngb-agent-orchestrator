"""Logging setup module for the agent orchestrator.

This module initializes Python's logging system based on the LOG_LEVEL
environment variable. Call setup_logging() once at application startup.

LOG_LEVEL controls the verbosity of all application logs:
  - DEBUG   : detailed information, useful for debugging
  - INFO    : general informational messages (default)
  - WARNING : warning messages for potentially harmful situations
  - ERROR   : error messages for serious problems
  - CRITICAL: critical messages for very serious problems

Example::

    from orchestrator.logging_setup import setup_logging
    setup_logging()
"""

import logging
import os
from contextvars import ContextVar

from orchestrator.paths import workflow_logs_dir

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
WORKFLOW_LOG_FILENAME = "workflow.log"

_active_workflow_file_handlers: ContextVar[frozenset[str]] = ContextVar(
    "active_workflow_file_handlers",
    default=frozenset(),
)


class _WorkflowContextFilter(logging.Filter):
    """Allow only records emitted while a specific workflow context is active."""

    def __init__(self, workflow_id: str) -> None:
        super().__init__()
        self._workflow_id = workflow_id

    def filter(self, record: logging.LogRecord) -> bool:
        from otel import get_workflow_id

        return get_workflow_id() == self._workflow_id


class WorkflowFileHandler(logging.FileHandler):
    """File handler carrying workflow metadata needed for deterministic detach."""

    def __init__(self, workflow_id: str) -> None:
        path = workflow_logs_dir(workflow_id, ensure_dir=True) / WORKFLOW_LOG_FILENAME
        super().__init__(path, mode="a", encoding="utf-8")
        self.workflow_id = workflow_id
        self._attached = False


def setup_logging() -> None:
    """Initialize Python logging based on LOG_LEVEL environment variable.

    Reads the LOG_LEVEL env var (default: INFO) and configures the root logger.
    This affects all application logging, including third-party libraries that
    use Python's logging module.

    Safe to call multiple times (idempotent).
    """
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper().strip()

    # Validate log level
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    invalid_log_level = log_level_str not in valid_levels
    if log_level_str not in valid_levels:
        invalid_value = log_level_str
        log_level_str = "INFO"
    else:
        invalid_value = ""

    log_level = getattr(logging, log_level_str)

    logging.basicConfig(
        level=log_level,
        format=LOG_FORMAT,
        datefmt=LOG_DATEFMT,
    )

    # Log the setup
    logger = logging.getLogger("ngb_orchestrator")
    if invalid_log_level:
        logger.warning(
            "Invalid LOG_LEVEL=%r. Using INFO. Valid values: %s",
            invalid_value,
            ", ".join(sorted(valid_levels)),
        )
    logger.info("Logging configured with LOG_LEVEL=%s", log_level_str)


def attach_workflow_file_handler(workflow_id: str) -> WorkflowFileHandler:
    """Attach a root handler for ``LOGS_DIR/<workflow_id>/workflow.log``.

    The handler is filtered by the current OTel workflow context so concurrent
    workflows running on different threads do not write into each other's log
    files.  Nested calls for the same workflow in the same context return an
    unattached handler to avoid duplicate records.
    """
    handler = WorkflowFileHandler(workflow_id)
    active = _active_workflow_file_handlers.get()
    if workflow_id in active:
        return handler

    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    handler.addFilter(_WorkflowContextFilter(workflow_id))
    root = logging.getLogger()
    root.addHandler(handler)
    _active_workflow_file_handlers.set(active | {workflow_id})
    handler._attached = True
    return handler


def detach_workflow_file_handler(handler: logging.Handler) -> None:
    """Detach and close a handler returned by ``attach_workflow_file_handler``."""
    root = logging.getLogger()
    if isinstance(handler, WorkflowFileHandler) and handler._attached:
        root.removeHandler(handler)
        active = _active_workflow_file_handlers.get()
        _active_workflow_file_handlers.set(active - {handler.workflow_id})
    handler.close()
