"""Cross-process locking and atomic JSON persistence shared by brain ledgers.

Every brain ledger is written by a request worker that may share the file with another worker
or another process. A :class:`LedgerLock` combines a thread re-entrant guard with an OS file
lock, and :func:`atomic_write_json` keeps the fsynced temp-file plus ``os.replace`` contract.
Engines decorate their mutating methods with :func:`engine_transaction` so a writer reloads
the ledger, applies one change and persists it while no other writer holds the lock.
"""

import functools
import json
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, Optional

from filelock import FileLock

logger = logging.getLogger(__name__)

LOCK_TIMEOUT_SECONDS = 10.0


class LedgerLock:
    """Serialize access to one ledger across threads and processes."""

    def __init__(self, path: str, timeout: float = LOCK_TIMEOUT_SECONDS):
        self.path = f"{path}.lock"
        self.timeout = float(timeout)
        self._thread_guard = threading.RLock()
        self._file_lock = FileLock(self.path, timeout=self.timeout)
        self._local = threading.local()

    @property
    def reentrant(self) -> bool:
        """Whether this thread already holds the lock (a nested engine call)."""
        return getattr(self._local, "depth", 0) > 0

    @contextmanager
    def held(self) -> Iterator[None]:
        with self._thread_guard:
            with self._file_lock:
                self._local.depth = getattr(self._local, "depth", 0) + 1
                try:
                    yield
                finally:
                    self._local.depth -= 1


def atomic_write_json(path: str, data: Dict[str, Any], *,
                      default: Optional[Callable[[Any], Any]] = None) -> None:
    """Write JSON through a fsynced temp file and atomic replace; the caller holds the lock."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, default=default)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError as exc:
            logger.debug("Could not remove ledger temp file %s: %s", temp_path, exc)
        raise


def ledger_transaction(method):
    """Run a store method under its own ledger lock."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.lock.held():
            return method(self, *args, **kwargs)

    return wrapper


def engine_transaction(method):
    """Run a synchronous engine mutation under its store lock, refreshing state once."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        lock = self.store.lock
        outer = not lock.reentrant
        with lock.held():
            if outer:
                _refresh(self)
            return method(self, *args, **kwargs)

    return wrapper


def engine_transaction_async(method):
    """Async variant of :func:`engine_transaction`.

    The OS lock is held in the event-loop thread for the duration of the method. That is
    acceptable because brain mutations are short and bounded; it must never wrap a slow
    provider or LLM call.
    """

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        lock = self.store.lock
        outer = not lock.reentrant
        with lock.held():
            if outer:
                _refresh(self)
            return await method(self, *args, **kwargs)

    return wrapper


def _refresh(instance) -> None:
    refresh = getattr(instance, "_reload_from_store", None)
    if refresh is not None:
        refresh()
