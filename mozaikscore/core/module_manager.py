# ==============================================================================
# FILE: mozaikscore/core/module_manager.py
# DESCRIPTION: Module scanning, dynamic loading, registry management, and
#              execute(data) dispatch.  Modules are the unit of business logic
#              callable by humans (REST) and agents (tool wrappers).
# ORIGIN: Migrated from mozaiks-core-public/backend/core/plugin_manager.py
#         Renamed: Plugin → Module throughout.
# ==============================================================================
import os
import sys
import json
import importlib
import importlib.util
import inspect
import logging
import asyncio
import time
import traceback
from functools import lru_cache
from pathlib import Path

from mozaikscore.core.config_loader import (
    get_module_registry,
    get_settings_config,
    get_notifications_config,
    get_config_path,
    reload_configs,
)

logger = logging.getLogger("mozaikscore.module_manager")

# ---------------------------------------------------------------------------
# Module path configuration
# ---------------------------------------------------------------------------
_MODULES_DIR_ENV = (os.getenv("MOZAIKS_MODULES_PATH") or "").strip()

if _MODULES_DIR_ENV:
    MODULES_DIR = os.path.abspath(_MODULES_DIR_ENV)
    logger.info("Using modules path: %s", MODULES_DIR)
else:
    MODULES_DIR = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "platform", "modules")
    )
    logger.info("MOZAIKS_MODULES_PATH not set, using default: %s", MODULES_DIR)

# Ensure importlib can find modules on sys.path
_MODULES_IMPORT_ROOT = os.path.abspath(os.path.join(MODULES_DIR, "..")) if MODULES_DIR else ""
for _p in (MODULES_DIR, _MODULES_IMPORT_ROOT):
    if _p and _p not in sys.path:
        sys.path.append(_p)


class ModuleManager:
    """Scans, loads, and dispatches business-logic modules."""

    def __init__(self):
        self.modules: dict = {}
        self._registry_cache: dict | None = None
        self._registry_last_refresh: float = 0
        self._registry_refresh_interval = 300  # 5 min
        self._refresh_lock = asyncio.Lock()
        # Build registry on construction (sync, no module loading yet)
        self.update_registry()

    async def init_async(self):
        """Async initialisation — call once after construction."""
        await self.load_modules()
        return self

    # ------------------------------------------------------------------
    # Registry scanning
    # ------------------------------------------------------------------
    def update_registry(self):
        """Scan the modules directory and rebuild module_registry.json."""
        registry_path = get_config_path() / "module_registry.json"

        try:
            if not os.path.isdir(MODULES_DIR):
                logger.warning("Modules directory does not exist: %s", MODULES_DIR)
                self._registry_cache = {"modules": []}
                return self._registry_cache

            module_dirs = [
                d
                for d in os.listdir(MODULES_DIR)
                if os.path.isdir(os.path.join(MODULES_DIR, d))
                and not d.startswith("_")
                and d.lower() not in ("registry", "__pycache__")
            ]

            registry: dict = {"modules": []}

            for mod_name in module_dirs:
                # Look for handler.py in the module directory
                handler_path = os.path.join(MODULES_DIR, mod_name, "handler.py")
                # Also support legacy layout: backend/logic.py or logic.py
                backend_logic_path = os.path.join(MODULES_DIR, mod_name, "backend", "logic.py")
                direct_logic_path = os.path.join(MODULES_DIR, mod_name, "logic.py")

                if os.path.exists(handler_path):
                    backend_path = f"modules.{mod_name}.handler"
                elif os.path.exists(backend_logic_path):
                    backend_path = f"modules.{mod_name}.backend.logic"
                elif os.path.exists(direct_logic_path):
                    backend_path = f"modules.{mod_name}.logic"
                else:
                    logger.warning("Skipping %s: no handler.py or logic.py found", mod_name)
                    continue

                metadata = {
                    "name": mod_name,
                    "display_name": mod_name.replace("_", " ").title(),
                    "description": f"Module: {mod_name}",
                    "version": "1.0.0",
                    "enabled": True,
                    "backend": backend_path,
                }

                # Try to read a module.json or plugin.json for richer metadata
                for meta_file in ("module.json", "plugin.json"):
                    meta_path = os.path.join(MODULES_DIR, mod_name, meta_file)
                    if os.path.exists(meta_path):
                        try:
                            with open(meta_path, "r", encoding="utf-8") as f:
                                extra = json.load(f)
                            metadata.update({
                                k: v
                                for k, v in extra.items()
                                if k in ("display_name", "description", "version", "required_tier", "enabled")
                            })
                        except Exception as exc:
                            logger.warning("Could not read %s: %s", meta_path, exc)
                        break

                registry["modules"].append(metadata)

            # Persist registry
            os.makedirs(registry_path.parent, exist_ok=True)
            with open(registry_path, "w", encoding="utf-8") as f:
                json.dump(registry, f, indent=2)

            self._registry_cache = registry
            self._registry_last_refresh = time.time()
            reload_configs()

            logger.info("Updated module registry: %d modules", len(registry["modules"]))
            return registry

        except Exception as exc:
            logger.error("Error updating module registry: %s", exc)
            registry = get_module_registry()
            if registry:
                self._registry_cache = registry
                return registry
            empty: dict = {"modules": []}
            self._registry_cache = empty
            return empty

    # ------------------------------------------------------------------
    # Module loading
    # ------------------------------------------------------------------
    async def load_modules(self):
        """Load all enabled modules from the registry."""
        registry = self._registry_cache or get_module_registry()
        self._registry_cache = registry

        for entry in registry.get("modules", []):
            if not entry.get("enabled", False):
                logger.info("Skipping disabled module: %s", entry.get("name"))
                continue

            mod_name = entry["name"]
            backend_path = entry.get("backend")
            if not backend_path:
                logger.error("Skipping %s: no backend specified", mod_name)
                continue

            if mod_name in self.modules:
                logger.debug("Module %s already loaded", mod_name)
                continue

            try:
                logger.info("Importing %s for module %s", backend_path, mod_name)
                module = importlib.import_module(backend_path)
                self.modules[mod_name] = {
                    "module": module,
                    "config": entry,
                    "load_time": time.time(),
                }
                await self._register_module_notifications(mod_name)
                logger.info("Loaded module: %s", mod_name)
            except Exception as exc:
                logger.error("Error loading module %s: %s", mod_name, exc)
                logger.error(traceback.format_exc())

    # ------------------------------------------------------------------
    # Execute dispatch
    # ------------------------------------------------------------------
    async def execute_module(self, module_name: str, data: dict) -> dict:
        """Execute a module's ``execute(data)`` (or ``run(data)``) entry point."""
        if module_name not in self.modules:
            logger.error(
                "Module %s not found. Available: %s",
                module_name,
                list(self.modules.keys()),
            )
            return {"error": f"Module '{module_name}' not found"}

        try:
            mod = self.modules[module_name]["module"]

            func = getattr(mod, "execute", None) or getattr(mod, "run", None)
            if func is None:
                return {"error": f"Module '{module_name}' has no execute() or run()"}

            if inspect.iscoroutinefunction(func):
                return await func(data)
            return func(data)

        except Exception as exc:
            logger.error("Error executing module %s: %s", module_name, exc)
            logger.error(traceback.format_exc())
            return {"error": f"Error executing module '{module_name}': {exc}"}

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    async def refresh_modules(self) -> dict:
        """Rescan and reload modules (rate-limited to refresh interval)."""
        async with self._refresh_lock:
            elapsed = time.time() - self._registry_last_refresh
            if elapsed < self._registry_refresh_interval:
                logger.info(
                    "Skipping module refresh (last refresh %ds ago)", int(elapsed)
                )
                return {"message": "Refresh skipped — too recent"}

            previous = set(self.modules.keys())
            self.update_registry()
            await self.load_modules()
            current = set(self.modules.keys())

            new_modules = current - previous
            logger.info(
                "Refreshed modules. Total: %d, new: %s",
                len(self.modules),
                list(new_modules),
            )
            return {
                "message": "Modules refreshed",
                "total_modules": len(self.modules),
                "new_modules": list(new_modules),
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @lru_cache(maxsize=128)
    def get_module_metadata(self, module_name: str) -> dict | None:
        registry = self._registry_cache or get_module_registry()
        for entry in registry.get("modules", []):
            if entry.get("name") == module_name:
                return entry
        return None

    async def check_module_exists(self, module_name: str) -> bool:
        if not self._registry_cache or (
            time.time() - self._registry_last_refresh > self._registry_refresh_interval
        ):
            self.update_registry()
        for entry in (self._registry_cache or {}).get("modules", []):
            if entry.get("name") == module_name and entry.get("enabled", True):
                return True
        return False

    async def ensure_module_loaded(self, module_name: str) -> bool:
        if module_name in self.modules:
            return True
        if not await self.check_module_exists(module_name):
            return False
        entry = next(
            (m for m in (self._registry_cache or {}).get("modules", []) if m.get("name") == module_name),
            None,
        )
        if not entry or not entry.get("backend"):
            return False
        try:
            mod = importlib.import_module(entry["backend"])
            self.modules[module_name] = {
                "module": mod,
                "config": entry,
                "load_time": time.time(),
            }
            await self._register_module_notifications(module_name)
            logger.info("Loaded module: %s", module_name)
            return True
        except Exception as exc:
            logger.error("Error loading module %s: %s", module_name, exc)
            return False

    # ------------------------------------------------------------------
    # Notification registration (updates settings_config.json)
    # ------------------------------------------------------------------
    async def _register_module_notifications(self, module_name: str):
        """Add notification toggle fields for *module_name* into settings_config.json."""
        try:
            settings_config = get_settings_config()
            notifications_config = get_notifications_config()

            if not settings_config:
                return

            notifications_section = next(
                (s for s in settings_config.get("profile_sections", []) if s.get("id") == "notifications"),
                None,
            )
            if not notifications_section:
                return

            notifications_section.setdefault("module_notification_fields", [])
            existing_fields = notifications_section["module_notification_fields"]

            # Gather declared notification types from notifications_config
            mod_notif_config = notifications_config.get("modules", notifications_config.get("plugins", {})).get(module_name, {})
            mod_notifications = mod_notif_config.get("notifications", [])

            if not mod_notifications:
                # Generic toggle
                generic_id = f"{module_name}_notifications"
                if not any(f.get("id") == generic_id for f in existing_fields):
                    display = module_name.replace("_", " ").title()
                    existing_fields.append({
                        "id": generic_id,
                        "module": module_name,
                        "label": f"{display} Notifications",
                        "type": "toggle",
                        "category": "modules",
                        "description": f"Receive notifications from {display}",
                        "required": False,
                        "editable": True,
                    })
            else:
                for notif in mod_notifications:
                    nid = notif.get("id")
                    if nid and not any(f.get("id") == nid for f in existing_fields):
                        existing_fields.append({
                            "id": nid,
                            "module": module_name,
                            "label": notif.get("label", nid),
                            "type": "toggle",
                            "category": notif.get("category", "modules"),
                            "description": notif.get("description", ""),
                            "required": False,
                            "editable": True,
                            "channels": notif.get("channels", ["in_app"]),
                            "default_enabled": notif.get("default_enabled", True),
                        })

            # Persist
            config_path = get_config_path() / "settings_config.json"
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(settings_config, f, indent=2)
            reload_configs()

        except Exception as exc:
            logger.error("Error registering notifications for module %s: %s", module_name, exc)


# ---------------------------------------------------------------------------
# Singleton (not yet initialised — call init_async() at startup)
# ---------------------------------------------------------------------------
module_manager = ModuleManager()
