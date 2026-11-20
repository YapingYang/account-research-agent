"""Tracing configuration and backend setup for LangSmith and Langfuse."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _get_secret(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()
    return str(value)


def _reset_langsmith_env_cache() -> None:
    """Drop LangSmith's cached env lookups so the variables set above take effect.

    langsmith.utils.get_env_var is lru_cached; if anything read it before the .env values were
    copied into os.environ (for example a previous Streamlit rerun), tracing would stay off.
    """
    try:
        from langsmith import utils as ls_utils

        ls_utils.get_env_var.cache_clear()
    except Exception:  # noqa: BLE001 - tracing is best-effort
        logger.debug("Could not reset LangSmith env cache", exc_info=True)


def configure_tracing(settings: Any) -> dict[str, Any]:
    """Configure the selected tracing backend and return metadata for the current run."""
    backend = str(getattr(settings, "tracing_backend", "none")).lower()
    if backend == "none":
        return {"backend": "none", "url": None}

    if backend == "langsmith":
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        project = os.environ.get("LANGSMITH_PROJECT") or getattr(settings, "langsmith_project", "account-research-demo")
        os.environ["LANGSMITH_PROJECT"] = project
        os.environ["LANGCHAIN_PROJECT"] = project
        key = os.environ.get("LANGSMITH_API_KEY") or _get_secret(getattr(settings, "langsmith_api_key", None))
        if not key:
            logger.warning(
                "LangSmith tracing is enabled but LANGSMITH_API_KEY is missing. Continuing without tracing; "
                "set LANGSMITH_API_KEY and LANGSMITH_PROJECT to enable it."
            )
            return {"backend": "langsmith", "url": None, "project": project, "enabled": False}
        os.environ["LANGSMITH_API_KEY"] = key
        os.environ["LANGCHAIN_API_KEY"] = key
        _reset_langsmith_env_cache()
        return {
            "backend": "langsmith",
            "project": project,
            "url": f"https://smith.langchain.com/o/default/projects/{project}",
            "enabled": True,
        }

    if backend == "langfuse":
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY") or _get_secret(
            getattr(settings, "langfuse_public_key", None)
        )
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY") or _get_secret(
            getattr(settings, "langfuse_secret_key", None)
        )
        host = os.environ.get("LANGFUSE_HOST") or getattr(settings, "langfuse_host", "https://cloud.langfuse.com")
        if not public_key or not secret_key:
            logger.warning(
                "Langfuse tracing is enabled but LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are missing. "
                "Continuing without tracing."
            )
            return {"backend": "langfuse", "url": None, "enabled": False}
        try:
            from langfuse import Langfuse
            from langfuse.langchain import CallbackHandler
        except ImportError:
            logger.warning(
                "Langfuse tracing is enabled but the package is not installed. "
                'Install it with `pip install -e ".[tracing]"` or `pip install langfuse`.'
            )
            return {"backend": "langfuse", "url": None, "enabled": False}

        client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
        callback = CallbackHandler()
        return {
            "backend": "langfuse",
            "url": None,
            "client": client,
            "callback": callback,
            "enabled": True,
        }

    return {"backend": backend, "url": None, "enabled": False}
