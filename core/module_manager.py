import importlib
import json
import logging
import os
import sys
import tempfile
import threading

from fastapi import FastAPI
from starlette.routing import Mount

from core.flow_runner import FlowRunner
from core.paths import project_path
from core.settings import settings

logger = logging.getLogger(__name__)

MODULES_DIR = project_path("modules")

# Issue 10/9: Module allowlist for hot-loading
# Note: This constant is kept for backwards compatibility but is now 
# loaded from settings.json at runtime. The settings value takes precedence.
# Set to ["chat", "memory", "knowledge_base"] to restrict which modules can be loaded
MODULE_ALLOWLIST = []  # Default empty = allow all (backwards compatibility)


def _write_manifest(meta_path: str, meta: dict) -> None:
    """Replace a module.json atomically so readers never see a truncated manifest.

    Serializing first means an unserializable value fails before the file is touched.
    """
    payload = json.dumps(meta, indent=4)
    fd, tmp_path = tempfile.mkstemp(prefix=".module-", suffix=".tmp",
                                    dir=os.path.dirname(meta_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, meta_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError as exc:
            logger.debug("Could not remove temp manifest %s: %s", tmp_path, exc)
        raise

class ModuleManager:
    def __init__(self, app: FastAPI, modules_dir=MODULES_DIR):
        self.modules_dir = modules_dir
        self.app = app
        # Use RLock for reentrant locking to allow nested lock acquisition
        self.lock = threading.RLock()
        self.modules = self._discover_modules()
        # Runtime-only tracking for load errors (not persisted)
        self._load_errors = {}
        # Track which module IDs have been imported by this manager at least once.
        # Used to distinguish an initial load (don't clear sys.modules) from a
        # hot-reload after unload (clear sys.modules to pick up code changes).
        self._loaded_once: set = set()

    def _discover_modules(self):
        """Finds all potential modules in the modules directory."""
        modules = {}
        if not os.path.exists(self.modules_dir):
            os.makedirs(self.modules_dir)
            return {}

        for name in os.listdir(self.modules_dir):
            module_path = os.path.join(self.modules_dir, name)
            if os.path.isdir(module_path):
                # Skip if DISABLED file exists
                if os.path.exists(os.path.join(module_path, "DISABLED")):
                    continue
                meta_path = os.path.join(module_path, "module.json")
                if os.path.exists(meta_path):
                    with open(meta_path, "r", encoding="utf-8") as f:
                        try:
                            meta = json.load(f)
                            meta['id'] = name
                            # Don't persist load_error from file - it's runtime-only
                            meta.pop('load_error', None)
                            modules[name] = meta
                        except json.JSONDecodeError:
                            logger.warning(f"Could not decode module.json for {name}")
        return modules

    def load_enabled_modules(self):
        """Loads routers for all modules marked as enabled."""
        for name, meta in self.modules.items():
            if meta.get("enabled", False):
                self._load_module_router(name)

    def _load_module_router(self, module_id: str):
        """Imports a module and adds its router to the app."""
        # Issue 9/10: Check module allowlist before loading
        # Load from settings first, fall back to constant for backwards compatibility
        runtime_allowlist = settings.get("module_allowlist", MODULE_ALLOWLIST)
        if runtime_allowlist and module_id not in runtime_allowlist:
            logger.warning(f"[ModuleManager] Module '{module_id}' not in allowlist, skipping load. Allowed: {runtime_allowlist}")
            return False
        
        # Use lock to prevent concurrent loading of the same module
        with self.lock:
            # Double-check after acquiring lock to prevent double registration
            # Check both Mount objects by path AND regular routes by tags
            module_prefix = f"/{module_id}"
            
            def is_module_loaded(route):
                # Check Mount objects by exact path match OR startswith with slash boundary
                if isinstance(route, Mount):
                    if route.path == module_prefix or route.path.startswith(module_prefix + "/"):
                        return True
                # Check regular routes by tags
                if hasattr(route, "tags") and module_id in route.tags:
                    return True
                # FastAPI defers include_router() expansion into an _IncludedRouter
                # wrapper, so the module's tags and prefix live in include_context
                # until the first request.
                context = getattr(route, "include_context", None)
                if context is not None:
                    if module_id in (getattr(context, "tags", None) or []):
                        return True
                    prefix = getattr(context, "prefix", "") or ""
                    if prefix == module_prefix or prefix.startswith(module_prefix + "/"):
                        return True
                return False
            
            if any(is_module_loaded(r) for r in self.app.router.routes):
                return True

            # Only flush sys.modules on a *re-load* — i.e. when this manager has
            # already imported the module once before (after a prior _unload_module_router
            # call).  On the very first load we must NOT clear existing sys.modules
            # entries: test collection may have already imported submodules such as
            # modules.tools.sandbox, and evicting them creates new module objects whose
            # top-level function definitions differ from previously-captured references
            # (causing PicklingError in multiprocessing, stale patch() targets, etc.).
            if module_id in self._loaded_once:
                # Hot-reload path: flush stale bytecode so code changes are picked up.
                modules_to_remove = [
                    key for key in list(sys.modules.keys())
                    if key.startswith(f"modules.{module_id}")
                ]
                for key in modules_to_remove:
                    del sys.modules[key]

            try:
                module = importlib.import_module(f"modules.{module_id}")
                self._loaded_once.add(module_id)
                if hasattr(module, "router"):
                    self.app.include_router(module.router, prefix=f"/{module_id}", tags=[module_id])
                    logger.info(f"Hot-loaded module router: {module_id}")
                    # Clear runtime load error (use runtime-only dict, don't touch module metadata)
                    self._load_errors[module_id] = None
                    # Also clear from in-memory module to ensure it's never persisted
                    self.modules[module_id].pop('load_error', None)
                    return True
            except Exception as e:
                logger.error(f"Failed to load module router for {module_id}: {e}")
                # Store load error in runtime-only tracking only, NOT in module metadata
                self._load_errors[module_id] = str(e)
                # IMPORTANT: Do NOT set self.modules[module_id]['load_error'] here!
                # This ensures it can never be persisted to disk
            return False

    def _unload_module_router(self, module_id: str):
        """Finds and removes a module's router from the app."""
        initial_route_count = len(self.app.router.routes)
        
        # FIX: Handle both regular routes (have tags) and Mount objects (don't have tags)
        # Mount objects are identified by path pattern
        module_prefix = f"/{module_id}"
        
        def should_keep_route(route):
            # Check regular routes by tags
            if hasattr(route, "tags") and module_id in route.tags:
                return False
            # Check Mount objects by exact path match OR startswith with slash boundary
            # This prevents unloading "tool" from also removing "tools", "toolbar", etc.
            if isinstance(route, Mount):
                # Exact match: /tool
                # Sub-path: /tool/something
                # But NOT /toolbar, /toolbox, etc.
                if route.path == module_prefix or route.path.startswith(module_prefix + "/"):
                    return False
            # FastAPI's deferred include wrapper carries the module identity in
            # include_context rather than on an expanded APIRoute.
            context = getattr(route, "include_context", None)
            if context is not None:
                if module_id in (getattr(context, "tags", None) or []):
                    return False
                prefix = getattr(context, "prefix", "") or ""
                if prefix == module_prefix or prefix.startswith(module_prefix + "/"):
                    return False
            return True
        
        self.app.router.routes = [route for route in self.app.router.routes if should_keep_route(route)]
        
        if len(self.app.router.routes) < initial_route_count:
            logger.info(f"Hot-unloaded module router: {module_id}")
            return True
        return False

    def get_all_modules(self):
        """Returns a list of all discovered modules and their metadata, sorted by order key."""
        # Sort by the 'order' key in the metadata, defaulting to a high number if not present.
        with self.lock:
            return sorted(list(self.modules.values()), key=lambda m: m.get('order', 999))

    def get_module(self, module_id: str):
        """Returns module metadata with proper locking to avoid race conditions."""
        with self.lock:
            return self.modules.get(module_id)

    def _toggle_module(self, module_id: str, enabled: bool):
        """Enables or disables a module, updating the running app and the metadata file."""
        with self.lock:
            if module_id not in self.modules:
                return None

            if enabled:
                self._load_module_router(module_id)
            else:
                self._unload_module_router(module_id)

            self.modules[module_id]['enabled'] = enabled
            
            # FIX: When saving, don't include load_error (runtime-only)
            meta_to_save = {k: v for k, v in self.modules[module_id].items() if k != 'load_error'}
            
            meta_path = os.path.join(self.modules_dir, module_id, "module.json")
            _write_manifest(meta_path, meta_to_save)
                
            # Clear the FlowRunner cache to ensure no stale executor classes are used
            # (FlowRunner handles its own internal state, so calling this static method is safe)
            FlowRunner.clear_cache()

        return self.modules[module_id]

    def reorder_modules(self, module_ids: list):
        """Updates the 'order' of only modules whose order actually changed."""
        import time
        start_time = time.time()
        
        # Build list of changes under lock, then release lock before file I/O
        with self.lock:
            # Create a map for quick lookups
            id_to_meta = {m['id']: m for m in self.modules.values()}
            
            # Build diff first - only track modules whose order actually changed
            modules_to_write = []
            
            for index, module_id in enumerate(module_ids):
                if module_id in id_to_meta:
                    current_order = id_to_meta[module_id].get('order')
                    # Only track if order is different
                    if current_order != index:
                        id_to_meta[module_id]['order'] = index
                        modules_to_write.append(module_id)
            
            # Update in-memory modules
            for module_id in modules_to_write:
                self.modules[module_id]['order'] = id_to_meta[module_id].get('order')
        
        # File I/O outside lock to avoid blocking other operations
        for module_id in modules_to_write:
            meta_path = os.path.join(self.modules_dir, module_id, "module.json")
            if os.path.exists(meta_path):
                # Don't include load_error in saved metadata
                meta_to_save = {k: v for k, v in id_to_meta[module_id].items() if k != 'load_error'}
                _write_manifest(meta_path, meta_to_save)
        
        # Log warning if operation took too long (potential lock contention)
        elapsed = time.time() - start_time
        if elapsed > 1.0:  # Warn if taking more than 1 second
            logger.warning(f"reorder_modules took {elapsed:.2f}s - consider optimizing to reduce lock hold time")

    def enable_module(self, module_id: str):
        return self._toggle_module(module_id, True)

    def disable_module(self, module_id: str):
        return self._toggle_module(module_id, False)

    def update_module_config(self, module_id: str, new_config: dict):
        """Updates the configuration for a specific module."""
        with self.lock:
            if module_id not in self.modules:
                return None
            
            self.modules[module_id]['config'] = new_config
            
            # FIX: Don't persist load_error - it's runtime-only
            # Only save config, enabled, order - not load_error
            meta_to_save = {
                k: v for k, v in self.modules[module_id].items() 
                if k not in ('load_error',)
            }
            
            meta_path = os.path.join(self.modules_dir, module_id, "module.json")
            _write_manifest(meta_path, meta_to_save)
            return self.modules[module_id]
