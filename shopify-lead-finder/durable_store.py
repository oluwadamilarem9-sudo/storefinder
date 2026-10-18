"""
Phase C durable write buffer.

Buffers lead / rejected / processed outcomes and flushes them in batches to
the account database. Used when DISCOVERY_DURABLE_STORE is postgres or dual.

Preserves Phase B rules: abandoned (timed-out) domains are dropped from the
buffer and never flushed. Heartbeat cadence may trigger a flush.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import config


class DurableWriteBuffer:
    """Thread-safe in-memory buffer for durable account-database writes."""

    def __init__(
        self,
        *,
        project_id: str,
        user_id: str,
        run_id: str,
        is_abandoned: Callable[[str], bool] | None = None,
        batch_size: int | None = None,
        flusher: Callable[..., None] | None = None,
    ) -> None:
        self.project_id = str(project_id)
        self.user_id = str(user_id)
        self.run_id = str(run_id)
        self.is_abandoned = is_abandoned or (lambda _domain: False)
        self.batch_size = max(int(batch_size or config.DURABLE_FLUSH_BATCH_SIZE or 10), 1)
        self._flusher = flusher
        self._lock = threading.Lock()
        self._leads: list[dict[str, Any]] = []
        self._rejected: list[dict[str, Any]] = []
        self._processed: list[dict[str, Any]] = []
        self._pending = 0

    def record_lead(self, item: dict[str, Any]) -> None:
        domain = str(item.get("domain") or "").strip()
        if not domain or self.is_abandoned(domain):
            return
        with self._lock:
            self._leads.append(dict(item))
            self._pending += 1
            should_flush = self._pending >= self.batch_size
        if should_flush:
            self.flush()

    def record_rejected(self, item: dict[str, Any]) -> None:
        domain = str(item.get("domain") or "").strip()
        if not domain or self.is_abandoned(domain):
            return
        with self._lock:
            self._rejected.append(dict(item))
            self._pending += 1
            should_flush = self._pending >= self.batch_size
        if should_flush:
            self.flush()

    def record_processed(self, item: dict[str, Any]) -> None:
        domain = str(item.get("domain") or "").strip()
        if not domain or self.is_abandoned(domain):
            return
        with self._lock:
            self._processed.append(dict(item))
            self._pending += 1
            should_flush = self._pending >= self.batch_size
        if should_flush:
            self.flush()

    def flush(self) -> dict[str, int]:
        with self._lock:
            leads = self._leads
            rejected = self._rejected
            processed = self._processed
            self._leads = []
            self._rejected = []
            self._processed = []
            self._pending = 0
        leads = [row for row in leads if not self.is_abandoned(str(row.get("domain") or ""))]
        rejected = [row for row in rejected if not self.is_abandoned(str(row.get("domain") or ""))]
        processed = [row for row in processed if not self.is_abandoned(str(row.get("domain") or ""))]
        if not leads and not rejected and not processed:
            return {"leads": 0, "rejected": 0, "processed": 0}
        flusher = self._flusher
        if flusher is None:
            import account_db

            flusher = account_db.flush_durable_batch
        flusher(
            self.project_id,
            self.user_id,
            self.run_id,
            leads,
            rejected,
            processed,
        )
        return {"leads": len(leads), "rejected": len(rejected), "processed": len(processed)}

    def maybe_flush_on_heartbeat(self) -> dict[str, int]:
        """Flush any pending rows when a Phase B heartbeat fires."""
        with self._lock:
            pending = self._pending
        if pending <= 0:
            return {"leads": 0, "rejected": 0, "processed": 0}
        return self.flush()
