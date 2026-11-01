"""Logging setup (implemented in milestone 1)."""

import logging


def configure_logging(level: str) -> None:
    """Configure application logging; milestone 1 implementation point."""
    logging.basicConfig(level=level.upper())
