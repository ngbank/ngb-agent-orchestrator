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

    # Configure basic logging format
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
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
