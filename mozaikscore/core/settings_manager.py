# ==============================================================================
# FILE: mozaikscore/core/settings_manager.py
# DESCRIPTION: Per-user settings persistence (MongoDB).  Notification preference
#              management.  Publishes events on settings changes.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/settings_manager.py
# ==============================================================================
import json
import logging
from pathlib import Path

from fastapi import HTTPException

from mozaikscore.core.database import get_settings_collection
from mozaikscore.core.config_loader import get_config_path, get_settings_config as _get_settings_config_raw
from mozaikscore.core.event_bus import event_bus

logger = logging.getLogger("mozaikscore.settings_manager")


class SettingsManager:
    def __init__(self):
        self.settings_config = self._load_settings_config()

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------
    def _load_settings_config(self) -> dict:
        return _get_settings_config_raw() or {"profile_sections": []}

    def refresh_settings_config(self) -> dict:
        self.settings_config = self._load_settings_config()
        return self.settings_config

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    async def get_user_settings(self, user_id: str) -> dict:
        coll = get_settings_collection()
        doc = await coll.find_one({"user_id": user_id})
        if not doc:
            doc = {
                "user_id": user_id,
                "module_settings": {},
                "notification_preferences": {},
            }
        return doc

    async def get_module_settings(self, user_id: str, module_name: str) -> dict:
        user_settings = await self.get_user_settings(user_id)
        return user_settings.get("module_settings", {}).get(module_name, {})

    async def save_module_settings(self, user_id: str, module_name: str, settings_data: dict) -> dict:
        if not module_name or not isinstance(module_name, str):
            raise HTTPException(status_code=400, detail="Invalid module name")

        user_settings = await self.get_user_settings(user_id)
        user_settings.setdefault("module_settings", {})[module_name] = settings_data

        coll = get_settings_collection()
        await coll.update_one(
            {"user_id": user_id},
            {"$set": user_settings},
            upsert=True,
        )

        event_bus.publish("settings_updated", {"user_id": user_id, "module": module_name})
        logger.info("Saved settings for module %s, user %s", module_name, user_id)
        return {"success": True, "message": "Settings saved"}

    # ------------------------------------------------------------------
    # Notification preferences
    # ------------------------------------------------------------------
    async def get_notification_preferences(self, user_id: str) -> dict:
        user_settings = await self.get_user_settings(user_id)
        prefs = user_settings.get("notification_preferences", {})
        if not prefs:
            prefs = self._default_notification_preferences()
        return prefs

    def _default_notification_preferences(self) -> dict:
        defaults: dict = {}
        section = next(
            (s for s in self.settings_config.get("profile_sections", []) if s.get("id") == "notifications"),
            None,
        )
        if not section:
            return defaults
        all_fields = section.get("fields", []) + section.get("module_notification_fields", section.get("plugin_notification_fields", []))
        for field in all_fields:
            fid = field.get("id")
            if fid and field.get("type") == "toggle":
                defaults[fid] = {"enabled": True, "frequency": "daily"}
        return defaults

    async def save_notification_preferences(self, user_id: str, preferences: dict) -> dict:
        try:
            config = self.refresh_settings_config()
            section = next(
                (s for s in config.get("profile_sections", []) if s.get("id") == "notifications"),
                None,
            )
            if not section:
                logger.error("No notifications section in settings config")
                return {}

            all_fields = section.get("fields", []) + section.get("module_notification_fields", section.get("plugin_notification_fields", []))
            valid_ids = {f["id"] for f in all_fields if f.get("id") and f.get("type") == "toggle"}

            valid_prefs = {}
            for pid, pdata in preferences.items():
                if pid in valid_ids:
                    valid_prefs[pid] = {
                        "enabled": bool(pdata.get("enabled", True)),
                        "frequency": pdata.get("frequency", "daily"),
                    }

            user_settings = await self.get_user_settings(user_id)
            user_settings["notification_preferences"] = valid_prefs

            coll = get_settings_collection()
            await coll.update_one(
                {"user_id": user_id},
                {"$set": user_settings},
                upsert=True,
            )

            event_bus.publish("notification_preferences_updated", {"user_id": user_id})
            return valid_prefs

        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Error saving notification prefs for %s: %s", user_id, exc)
            raise HTTPException(status_code=500, detail=f"Failed to update notification preferences: {exc}")

    # ------------------------------------------------------------------
    # Visibility filtering (subscription gating)
    # ------------------------------------------------------------------
    async def update_settings_visibility(self, monetization_enabled: bool, user_id: str) -> dict:
        sections = self.settings_config.get("profile_sections", [])
        updated = []
        for section in sections:
            if section.get("id") == "notifications" and monetization_enabled:
                section_copy = dict(section)
                module_fields = section.get("module_notification_fields", section.get("plugin_notification_fields", []))
                filtered = []
                for field in module_fields:
                    mod_name = field.get("module") or field.get("plugin")
                    if mod_name:
                        from mozaikscore.core.subscription_manager import subscription_manager
                        if await subscription_manager.is_module_accessible(user_id, mod_name):
                            filtered.append(field)
                section_copy["module_notification_fields"] = filtered
                updated.append(section_copy)
            else:
                updated.append(section)
        return {"profile_sections": updated}


# Singleton
settings_manager = SettingsManager()
