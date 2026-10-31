"""
Compatibility facade for the shared account service.

Existing imports (``import account_db``) keep working. Implementation lives in
``services.account_service``. Prefer importing from ``services.account_service``
in new code (Phase 1B+).

Mutable module state such as ``_engine`` / ``_Session`` is resolved via
``__getattr__`` so ``reset_account_db_engine()`` stays visible to callers.
"""

from __future__ import annotations

from services import account_service as _impl

# Copied so app.py's ``getattr(account_db, "_ACCOUNT_DB_GENERATION")`` sees it
# without relying on __getattr__ during early import/reload checks.
_ACCOUNT_DB_GENERATION = _impl._ACCOUNT_DB_GENERATION


def __getattr__(name: str):
    """Proxy all other names to ``services.account_service``."""
    return getattr(_impl, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_impl)))
