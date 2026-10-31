# Concurrency Model Documentation

## Overview

NeuroCore uses a hybrid concurrency model with both synchronous and asynchronous code. This document outlines the lock ordering rules to prevent deadlocks.

## Lock Types Used

### 1. threading.RLock
Used in synchronous code paths (reentrant — safe for nested acquisition):
- `core/flow_manager.py` — `FlowManager.lock`
- `core/module_manager.py` — `ModuleManager.lock`
- `core/settings.py` — `SettingsManager.lock`
- `core/session_manager.py` — `SessionPersistenceManager._sync_lock`

### 2. threading.Lock (non-reentrant)
- `core/observability.py` — `Metrics._lock` (single-level; not RLock)
- `core/session_manager.py` — `SessionManager._lock` (instance-level)
- `core/session_manager.py` — `_init_lock` (module-level singleton guard)
- `modules/chat/sessions.py` — `SessionManager._lock` (all session operations; async callers bridge via `asyncio.to_thread`)

### 3. asyncio.Lock
Used in asynchronous code paths (must be `await`ed inside `async` functions):
- `core/llm.py` — `_client_lock` (lazy-initialized, module-level)
- `core/flow_runner.py` — `_cache_lock` (per event-loop: stored in dict keyed by `id(loop)`)
- `modules/reasoning_book/service.py` — `_lock`
- `modules/chat/sessions.py` — async callers use `asyncio.to_thread` to bridge into the threading.Lock

### 4. No Lock
- `core/debug.py` — `DebugLogger` (explicitly not thread-safe for compound operations)

---

## Lock Ordering Rules

To prevent deadlocks, follow these rules strictly.

### Rule 1: Never mix threading and asyncio locks in the same code path
- If you hold a `threading.RLock`, never `await` an asyncio operation that might try to acquire an `asyncio.Lock`.
- If you hold an `asyncio.Lock`, never call synchronous code that might try to acquire a `threading.RLock`.

### Rule 2: Acquire locks in consistent order
When multiple locks must be acquired:
1. **Always** acquire `threading.RLock` before `asyncio.Lock` (never the reverse)
2. If multiple `threading.RLock`s are needed, acquire in this order:
   1. `module_manager._lock`
   2. `flow_manager._lock`
   3. `settings._lock`
3. If multiple `asyncio.Lock`s are needed, acquire in alphabetical order by variable name

### Rule 3: Use context managers
Always use `with` for threading locks and `async with` for asyncio locks:
```python
# Good
with self.lock:
    # do work

# Good
async with self._async_lock:
    # do async work

# Bad — will cause deadlock or thread starvation
with self.lock:
    await something()  # Don't await while holding a threading lock
```

### Rule 4: Keep critical sections small
Minimize the time locks are held to reduce the deadlock window. Never perform I/O, LLM calls, or network requests inside a critical section.

---

## Flow Manager, Module Manager, and Settings Manager

All three use `threading.RLock` because:
1. They are accessed from synchronous FastAPI route handlers.
2. Their operations involve file I/O which is synchronous.
3. The asyncio-based FlowRunner accesses them during initialization (synchronous phase).

---

## FlowRunner Cache Lock (`_cache_lock`)

The FlowRunner's `_cache_lock` is a per-event-loop `asyncio.Lock`. Because multiple event loops can exist during testing (each `pytest` test may create a new loop), the lock is stored in a dict keyed by `id(loop)` rather than as a single instance. When a new event loop is detected, a fresh lock is created for it. This avoids "attached to a different loop" errors across test runs.

---

## ModuleManager Hot-Reload Safety (`_loaded_once`)

`ModuleManager` tracks which module IDs have been imported using the `_loaded_once` set. On first load, it does **not** flush `sys.modules` entries for the module — this is critical in test environments where submodules (e.g., `modules.tools.sandbox`) may have already been imported. On subsequent reloads (after an explicit unload), it flushes `sys.modules` to pick up code changes. **Never clear `_loaded_once` directly**; use `_unload_module_router` followed by `_load_module_router` for proper hot-swapping.

---

## Session Manager Locks

`core/session_manager.py` has two distinct lock scopes:
- `_lock` (`threading.Lock`) — protects the in-memory `SessionManager` state per instance
- `_sync_lock` (`threading.RLock`) — protects `SessionPersistenceManager` file operations
- `_init_lock` (`threading.Lock`) — module-level singleton guard for `get_session_manager()`

---

## MessagingService Threading Model

`modules/messaging_bridge/service.py` runs three platform listener threads:
- **Telegram**: long-polling loop in a `threading.Thread` (daemon=True)
- **Discord**: WebSocket event loop in a `threading.Thread` (daemon=True)
- **Signal**: polling loop in a `threading.Thread` (daemon=True)
- **WhatsApp**: no polling thread — webhook-driven only

Each listener calls `_run_flow()` which uses `asyncio.run_coroutine_threadsafe()` to dispatch flow execution onto the main event loop. The `_SessionStore` uses a `threading.Lock` to protect the session dict.

---

## Best Practices

1. **Document lock hierarchies** in each module's docstring
2. **Use RLock** instead of Lock when the same thread might acquire the lock multiple times
3. **Never `await` while holding a `threading.Lock`** — the lock is not released during the await, blocking other threads
4. **Use `asyncio.to_thread()`** to call blocking file/DB operations from async context without holding any lock
5. **Log warnings** when operations take longer than 1 second (potential lock contention)
6. **Never access `settings.settings` dict directly** — always use `settings.get()` and `settings.update()`

---

## Testing for Deadlocks

Run the concurrency stress tests:
```bash
pytest tests/test_core_concurrency.py -v
pytest tests/test_core_robustness.py -v
```

These tests verify:
- Multiple threads can access FlowManager concurrently
- Multiple coroutines can access async locks concurrently
- FlowRunner input isolation (source nodes receive independent copies)
- No deadlocks occur under load
