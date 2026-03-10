# ==============================================================================
# FILE: mozaikscore/core/notifications_manager.py
# DESCRIPTION: Queue-based multi-channel notification delivery.
#              In-app (persistent), email (async), WebSocket (real-time).
#              Background queue processing with batching.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/notifications_manager.py
# ==============================================================================
import os
import json
import logging
import uuid
import asyncio
import traceback
from datetime import datetime

import aiohttp
from bson import ObjectId
from fastapi import HTTPException

from mozaikscore.core.database import get_users_collection, get_cached_document, with_retry, db_cache
from mozaikscore.core.event_bus import event_bus
from mozaikscore.core.websocket_manager import websocket_manager
from mozaikscore.core.config_loader import (
    get_config_path,
    get_notifications_config as _load_notifications_cfg,
    get_settings_config as _load_settings_cfg,
)

logger = logging.getLogger("mozaikscore.notifications_manager")

# Environment
HOSTING_SERVICE = os.getenv("HOSTING_SERVICE", "0") == "1"
EMAIL_SERVICE_URL = os.getenv("EMAIL_SERVICE_URL", "")
EMAIL_SERVICE_API_KEY = os.getenv("EMAIL_SERVICE_API_KEY", "")

# Constants
MAX_NOTIFICATIONS_PER_USER = 100
NOTIFICATION_BATCH_SIZE = 50
NOTIFICATION_CACHE_TTL = 300  # seconds


class NotificationsManager:
    def __init__(self):
        self.config: dict | None = None
        self.config_last_loaded: float = 0
        self.notification_types_cache: dict = {}
        self._email_semaphore = asyncio.Semaphore(5)
        self._notification_queue: asyncio.Queue = asyncio.Queue()
        self._is_processing = False
        self._processing_task: asyncio.Task | None = None
        self._config_root = get_config_path()

        self._load_config()
        self.register_event_handlers()

        logger.info("Notifications Manager initialized (HOSTING_SERVICE=%s)", HOSTING_SERVICE)

    # ------------------------------------------------------------------
    # Event handler registration
    # ------------------------------------------------------------------
    def register_event_handlers(self):
        event_bus.subscribe("subscription_updated", self.handle_subscription_update)
        event_bus.subscribe("subscription_canceled", self.handle_subscription_cancel)
        event_bus.subscribe("module_settings_updated", self.handle_module_settings_updated)
        logger.info("Notification event handlers registered")

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------
    def _load_config(self) -> dict:
        import time
        now = time.time()
        if self.config and (now - self.config_last_loaded) < NOTIFICATION_CACHE_TTL:
            return self.config

        try:
            self.config = _load_notifications_cfg() or {"categories": [], "settings": {}}
            self.config_last_loaded = now
            logger.info("Loaded notifications config with %d categories", len(self.config.get("categories", [])))
        except Exception:
            self.config = {"categories": [], "settings": {}}
        return self.config

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------
    @with_retry(max_retries=3, delay=1)
    async def get_user_notification_preferences(self, user_id: str) -> dict | None:
        try:
            users = get_users_collection()
            user = await get_cached_document(
                users, {"_id": ObjectId(user_id)}, cache_key=f"user_notif_prefs:{user_id}"
            )
            if not user:
                logger.error("User %s not found", user_id)
                return None
            prefs = user.get("notification_preferences", {})
            if not prefs:
                prefs = await self._get_default_preferences()
            return prefs
        except Exception as exc:
            logger.error("Error getting notification preferences for %s: %s", user_id, exc)
            return None

    async def _get_default_preferences(self) -> dict:
        settings_config = _load_settings_cfg() or {"profile_sections": []}
        if not self.config:
            self._load_config()
        default_enabled = self.config.get("settings", {}).get("default_enabled", True)
        default_prefs: dict = {}
        section = next(
            (s for s in settings_config.get("profile_sections", []) if s.get("id") == "notifications"), None
        )
        if not section:
            return default_prefs
        all_fields = section.get("fields", []) + section.get(
            "module_notification_fields", section.get("plugin_notification_fields", [])
        )
        for field in all_fields:
            fid = field.get("id")
            if fid and field.get("type") == "toggle":
                default_prefs[fid] = {
                    "enabled": field.get("default_enabled", default_enabled),
                    "frequency": "immediate",
                }
        return default_prefs

    @with_retry(max_retries=3, delay=1)
    async def update_notification_preferences(self, user_id: str, preferences: dict):
        try:
            settings_config = _load_settings_cfg() or {"profile_sections": []}
            section = next(
                (s for s in settings_config.get("profile_sections", []) if s.get("id") == "notifications"), None
            )
            if not section:
                logger.error("No notifications section in settings config")
                return False

            all_fields = section.get("fields", []) + section.get(
                "module_notification_fields", section.get("plugin_notification_fields", [])
            )
            valid_ids = {f["id"] for f in all_fields if f.get("id") and f.get("type") == "toggle"}

            valid_prefs = {}
            for pid, pdata in preferences.items():
                if pid in valid_ids:
                    valid_prefs[pid] = {"enabled": bool(pdata.get("enabled", True)), "frequency": "immediate"}

            users = get_users_collection()
            result = await users.update_one(
                {"_id": ObjectId(user_id)}, {"$set": {"notification_preferences": valid_prefs}}
            )
            db_cache.invalidate(f"user_notif_prefs:{user_id}")

            if result.modified_count == 0:
                logger.warning("No changes to notification prefs for user %s", user_id)

            event_bus.publish("notification_preferences_updated", {"user_id": user_id})
            return valid_prefs
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Error updating notification prefs for %s: %s", user_id, exc)
            raise HTTPException(status_code=500, detail=f"Failed to update notification preferences: {exc}")

    # ------------------------------------------------------------------
    # Notification config (UI facing)
    # ------------------------------------------------------------------
    async def get_notification_config(self, user_id: str | None = None, monetization_enabled: bool = False) -> dict:
        if not self.config:
            self._load_config()

        settings_config = _load_settings_cfg() or {"profile_sections": []}
        section = next(
            (s for s in settings_config.get("profile_sections", []) if s.get("id") == "notifications"), None
        )
        if not section:
            return self.config  # type: ignore[return-value]

        notification_fields = section.get("fields", [])
        module_fields = section.get("module_notification_fields", section.get("plugin_notification_fields", []))

        # Subscription-gated filtering
        if monetization_enabled and user_id:
            module_groups: dict[str, list] = {}
            for field in module_fields:
                mod = field.get("module") or field.get("plugin")
                if mod:
                    module_groups.setdefault(mod, []).append(field)
            filtered: list = []
            from mozaikscore.core.subscription_manager import subscription_manager

            for mod_name, fields in module_groups.items():
                if await subscription_manager.is_module_accessible(user_id, mod_name):
                    filtered.extend(fields)
            module_fields = filtered

        available_channels = ["in_app"]
        if HOSTING_SERVICE:
            available_channels.append("email")

        notifications = []
        for field in notification_fields:
            if field.get("type") == "toggle" and field.get("id"):
                notifications.append(
                    {
                        "id": field["id"],
                        "category": field.get("category", "system"),
                        "name": field.get("label", field["id"]),
                        "description": field.get("description", ""),
                        "default": True,
                        "channels": available_channels,
                        "frequencies": ["immediate"],
                    }
                )

        for field in module_fields:
            if field.get("type") == "toggle" and field.get("id"):
                field_channels = field.get("channels", available_channels)
                channels = [ch for ch in field_channels if ch in available_channels]
                notifications.append(
                    {
                        "id": field["id"],
                        "category": field.get("category", "modules"),
                        "name": field.get("label", field["id"]),
                        "description": field.get("description", ""),
                        "default": field.get("default_enabled", True),
                        "channels": channels,
                        "frequencies": ["immediate"],
                        "module": field.get("module") or field.get("plugin"),
                    }
                )

        return {
            "categories": self.config.get("categories", []),  # type: ignore[union-attr]
            "notifications": notifications,
            "email_service_enabled": HOSTING_SERVICE,
            "settings": self.config.get("settings", {}),  # type: ignore[union-attr]
        }

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------
    async def _send_email_notification(self, user_id: str, notification_type: str, title: str, message: str):
        async with self._email_semaphore:
            try:
                users = get_users_collection()
                user_data = await get_cached_document(
                    users, {"_id": ObjectId(user_id)}, cache_key=f"user_email:{user_id}"
                )
                if user_data and user_data.get("email"):
                    await self.send_email(
                        recipient=user_data["email"],
                        subject=title,
                        message=message,
                        notification_type=notification_type,
                    )
            except Exception as exc:
                logger.error("Failed to send async email notification: %s", exc)

    async def send_email(self, recipient: str, subject: str, message: str, notification_type: str | None = None) -> bool:
        if not HOSTING_SERVICE:
            logger.info("Email to %s skipped (HOSTING_SERVICE disabled)", recipient)
            return False
        if not EMAIL_SERVICE_URL:
            logger.info("Email to %s skipped (EMAIL_SERVICE_URL not configured)", recipient)
            return False

        payload = {"recipient": recipient, "subject": subject, "message": message, "notification_type": notification_type}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {EMAIL_SERVICE_API_KEY}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(EMAIL_SERVICE_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        logger.info("Email sent to %s", recipient)
                        return True
                    text = await resp.text()
                    logger.error("Email send failed: %d - %s", resp.status, text)
                    return False
        except (aiohttp.ClientConnectorError, asyncio.TimeoutError) as exc:
            logger.error("Email connection error: %s", exc)
            return False
        except Exception as exc:
            logger.error("Email service error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # create / queue
    # ------------------------------------------------------------------
    async def create_notification(
        self, user_id: str, notification_type: str, title: str, message: str, metadata: dict | None = None
    ) -> dict | None:
        try:
            await self._notification_queue.put(
                {"user_id": user_id, "type": notification_type, "title": title, "message": message, "metadata": metadata or {}}
            )
            if not self._is_processing:
                self.start_background_processing()
            return {"id": str(uuid.uuid4()), "queued": True}
        except Exception as exc:
            logger.error("Error creating notification for %s: %s", user_id, exc)
            return None

    async def create_bulk_notifications(
        self, user_ids: list[str], notification_type: str, title: str, message: str, metadata: dict | None = None
    ) -> list[str]:
        if not user_ids:
            return []
        ids: list[str] = []
        base = {"type": notification_type, "title": title, "message": message, "metadata": metadata or {}}
        for uid in user_ids:
            n = dict(base, user_id=uid)
            await self._notification_queue.put(n)
            ids.append(str(uuid.uuid4()))
        if not self._is_processing:
            self.start_background_processing()
        return ids

    # ------------------------------------------------------------------
    # Background processing
    # ------------------------------------------------------------------
    def start_background_processing(self):
        if not self._is_processing:
            self._is_processing = True
            self._processing_task = asyncio.create_task(self._process_notification_queue())
            logger.info("Started background notification processing")

    async def stop_background_processing(self):
        if self._is_processing and self._processing_task:
            self._is_processing = False
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped background notification processing")

    async def _process_notification_queue(self):
        while self._is_processing:
            try:
                batch: list[dict] = []
                for _ in range(min(NOTIFICATION_BATCH_SIZE, self._notification_queue.qsize() + 1)):
                    try:
                        item = await asyncio.wait_for(self._notification_queue.get(), timeout=0.1)
                        batch.append(item)
                    except asyncio.TimeoutError:
                        break
                if not batch:
                    await asyncio.sleep(1)
                    continue

                logger.info("Processing batch of %d notifications", len(batch))
                user_groups: dict[str, list] = {}
                for n in batch:
                    user_groups.setdefault(n["user_id"], []).append(n)
                for uid, notifs in user_groups.items():
                    await self._process_user_notifications(uid, notifs)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Notification queue error: %s", exc)
                await asyncio.sleep(5)

    async def _process_user_notifications(self, user_id: str, notifications: list[dict]):
        try:
            prefs = await self.get_user_notification_preferences(user_id) or await self._get_default_preferences()
            in_app: list[dict] = []
            email_list: list[dict] = []

            for n in notifications:
                ntype = n["type"]
                enabled = prefs.get(ntype, {}).get("enabled", True)
                if not enabled:
                    mod = self._get_module_from_notification_type(ntype)
                    if mod:
                        enabled = prefs.get(f"{mod}_notifications", {}).get("enabled", True)
                if not enabled:
                    continue

                channels = await self._get_notification_channels(ntype, user_id)
                obj = {
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "type": ntype,
                    "title": n["title"],
                    "message": n["message"],
                    "created_at": datetime.utcnow().isoformat(),
                    "read": False,
                    "metadata": n["metadata"],
                }
                if "in_app" in channels:
                    in_app.append(obj)
                if "email" in channels and HOSTING_SERVICE:
                    if prefs.get("email_notifications", {}).get("enabled", True):
                        email_list.append(obj)

            if in_app:
                await self._save_in_app_notifications(user_id, in_app)
                for notif in in_app:
                    await websocket_manager.send_to_user(
                        user_id, {"type": "notification", "subtype": "new", "data": notif}
                    )
            for notif in email_list:
                asyncio.create_task(
                    self._send_email_notification(user_id, notif["type"], notif["title"], notif["message"])
                )
        except Exception as exc:
            logger.error("Error processing notifications for %s: %s", user_id, exc)

    # ------------------------------------------------------------------
    # Channel resolution
    # ------------------------------------------------------------------
    def _get_module_from_notification_type(self, notification_type: str) -> str | None:
        if notification_type in self.notification_types_cache:
            return self.notification_types_cache[notification_type]
        core_types = {"subscription_updates", "security_alerts", "email_notifications"}
        if notification_type in core_types:
            self.notification_types_cache[notification_type] = None
            return None
        parts = notification_type.split("_")
        if len(parts) >= 2 and self.config:
            module_configs = self.config.get("modules", self.config.get("plugins", {}))
            for i in range(1, len(parts)):
                candidate = "_".join(parts[:i])
                if candidate in module_configs:
                    self.notification_types_cache[notification_type] = candidate
                    return candidate
            candidate = parts[0]
            self.notification_types_cache[notification_type] = candidate
            return candidate
        self.notification_types_cache[notification_type] = None
        return None

    async def _get_notification_channels(self, notification_type: str, user_id: str) -> list[str]:
        allowed = ["in_app"]
        core_types = {"subscription_updates", "security_alerts", "email_notifications"}
        if notification_type in core_types:
            if HOSTING_SERVICE:
                allowed.append("email")
            return allowed

        mod = self._get_module_from_notification_type(notification_type)
        if mod:
            settings_config = _load_settings_cfg() or {"profile_sections": []}
            section = next(
                (s for s in settings_config.get("profile_sections", []) if s.get("id") == "notifications"), None
            )
            if section:
                module_fields = section.get("module_notification_fields", section.get("plugin_notification_fields", []))
                field = next((f for f in module_fields if f.get("id") == notification_type), None)
                if field and "channels" in field:
                    channels = list(field["channels"])
                    if "email" in channels and not HOSTING_SERVICE:
                        channels.remove("email")
                    return channels
        return allowed

    # ------------------------------------------------------------------
    # In-app persistence
    # ------------------------------------------------------------------
    @with_retry(max_retries=3, delay=1)
    async def _save_in_app_notifications(self, user_id: str, notifications: list[dict]) -> bool:
        try:
            uid_obj = ObjectId(user_id) if not isinstance(user_id, ObjectId) else user_id
            users = get_users_collection()
            user = await users.find_one({"_id": uid_obj}, {"notifications": {"$slice": 1}, "_id": 1})
            if not user:
                logger.error("User %s not found, cannot save notifications", user_id)
                return False

            if len(notifications) > MAX_NOTIFICATIONS_PER_USER:
                notifications = notifications[:MAX_NOTIFICATIONS_PER_USER]

            result = await users.update_one(
                {"_id": uid_obj},
                {
                    "$push": {
                        "notifications": {
                            "$each": notifications,
                            "$sort": {"created_at": -1},
                            "$slice": -MAX_NOTIFICATIONS_PER_USER,
                        }
                    }
                },
            )
            if result.modified_count > 0:
                logger.info("Saved %d in-app notifications for %s", len(notifications), user_id)
                return True
            logger.warning("No notifications saved for %s", user_id)
            return False
        except Exception as exc:
            logger.error("Error saving in-app notifications: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Read / mark / delete
    # ------------------------------------------------------------------
    @with_retry(max_retries=3, delay=1)
    async def get_user_notifications(self, user_id: str, unread_only: bool = False, limit: int = 20, offset: int = 0) -> list:
        try:
            uid_obj = ObjectId(user_id) if not isinstance(user_id, ObjectId) else user_id
            users = get_users_collection()
            if unread_only:
                pipeline = [
                    {"$match": {"_id": uid_obj}},
                    {"$project": {"_id": 1, "notifications": {"$filter": {"input": "$notifications", "as": "n", "cond": {"$eq": ["$$n.read", False]}}}}},
                    {"$project": {"_id": 1, "notifications": {"$slice": ["$notifications", offset, limit]}}},
                ]
                result = await users.aggregate(pipeline).to_list(length=1)
                return result[0].get("notifications", []) if result else []
            else:
                user = await users.find_one({"_id": uid_obj}, {"notifications": {"$slice": [offset, limit]}, "_id": 1})
                return user.get("notifications", []) if user else []
        except Exception as exc:
            logger.error("Error getting notifications for %s: %s", user_id, exc)
            return []

    @with_retry(max_retries=3, delay=1)
    async def mark_notification_read(self, user_id: str, notification_id: str, read: bool = True) -> bool:
        try:
            users = get_users_collection()
            result = await users.update_one(
                {"_id": ObjectId(user_id), "notifications.id": notification_id},
                {"$set": {"notifications.$.read": read}},
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error("Error marking notification read for %s: %s", user_id, exc)
            return False

    @with_retry(max_retries=3, delay=1)
    async def mark_all_notifications_read(self, user_id: str) -> bool:
        try:
            users = get_users_collection()
            result = await users.update_one(
                {"_id": ObjectId(user_id)}, {"$set": {"notifications.$[].read": True}}
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error("Error marking all notifications read for %s: %s", user_id, exc)
            return False

    @with_retry(max_retries=3, delay=1)
    async def delete_notification(self, user_id: str, notification_id: str) -> bool:
        try:
            users = get_users_collection()
            result = await users.update_one(
                {"_id": ObjectId(user_id)}, {"$pull": {"notifications": {"id": notification_id}}}
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error("Error deleting notification for %s: %s", user_id, exc)
            return False

    # ------------------------------------------------------------------
    # Event handlers (bound in register_event_handlers)
    # ------------------------------------------------------------------
    async def handle_subscription_update(self, event_data: dict):
        user_id = event_data.get("user_id")
        plan = event_data.get("plan")
        if user_id and plan:
            await self.create_notification(
                user_id=user_id,
                notification_type="subscription_updates",
                title="Subscription Updated",
                message=f"Your subscription has been updated to the {plan} plan.",
                metadata={"plan": plan},
            )

    async def handle_subscription_cancel(self, event_data: dict):
        user_id = event_data.get("user_id")
        if user_id:
            await self.create_notification(
                user_id=user_id,
                notification_type="subscription_updates",
                title="Subscription Cancelled",
                message="Your subscription has been cancelled. Access continues until end of billing period.",
                metadata={"status": "cancelled"},
            )

    async def handle_module_settings_updated(self, event_data: dict):
        user_id = event_data.get("user_id")
        module_name = event_data.get("module") or event_data.get("plugin")
        if user_id and module_name:
            await self.create_notification(
                user_id=user_id,
                notification_type=f"{module_name}_settings_updated",
                title=f"{module_name.replace('_', ' ').title()} Settings Updated",
                message=f"Your settings for {module_name.replace('_', ' ').title()} have been updated.",
                metadata={"module": module_name},
            )


# Singleton
notifications_manager = NotificationsManager()


async def create_notification_indexes():
    """Create indexes for faster notification queries."""
    try:
        from pymongo import ASCENDING

        users = get_users_collection()
        await users.create_index([("notifications.id", ASCENDING)])
        await users.create_index([("notifications.read", ASCENDING)])
        await users.create_index([("notifications.created_at", ASCENDING)])
        logger.info("Created notification indexes")
    except Exception as exc:
        logger.error("Error creating notification indexes: %s", exc)
