# /backend/core/notifications_manager.py
import os
import json
import logging
import uuid
import aiohttp
import asyncio
from datetime import datetime, timedelta
from core.config.database import users_collection, get_cached_document, with_retry
from core.event_bus import event_bus
from bson import ObjectId
from fastapi import HTTPException
from pymongo import UpdateOne, ASCENDING, IndexModel
import time
import functools
import traceback
from core.websocket_manager import websocket_manager
from core.config.config_loader import get_config_path

logger = logging.getLogger("mozaiks_core.notifications_manager")

# Get email service configuration
HOSTING_SERVICE = os.getenv("HOSTING_SERVICE", "0") == "1"
EMAIL_SERVICE_URL = os.getenv("EMAIL_SERVICE_URL", "")
EMAIL_SERVICE_API_KEY = os.getenv("EMAIL_SERVICE_API_KEY", "")

# Notification constants
MAX_NOTIFICATIONS_PER_USER = 100  # Maximum notifications to store per user
NOTIFICATION_BATCH_SIZE = 50      # Number of notifications to process in a batch
NOTIFICATION_CACHE_TTL = 300      # Cache TTL for notification config (5 minutes)

class NotificationsManager:
    def __init__(self):
        self.config = None
        self.config_last_loaded = 0
        self.notification_types_cache = {}
        self._email_semaphore = asyncio.Semaphore(5)  # Limit concurrent email sends
        self._notification_queue = asyncio.Queue()
        self._is_processing = False
        self._processing_task = None
        self._config_root = get_config_path()
        
        # Initialize configuration
        self._load_config()
        
        # Manually subscribe event handlers so that 'self' is correctly bound
        self.register_event_handlers()
        
        # Debug environment variables
        logger.info(f"✅ Notifications Manager initialized with:")
        logger.info(f"  HOSTING_SERVICE = {HOSTING_SERVICE}")
        logger.info(f"  EMAIL_SERVICE_URL = {EMAIL_SERVICE_URL}")
        logger.info(f"  EMAIL_SERVICE_API_KEY = {'*****' if EMAIL_SERVICE_API_KEY else 'Not set'}")
        
        logger.info("✅ Notifications Manager initialized")

    def register_event_handlers(self):
        """
        Register class-based event handlers so each is bound to 'self'
        """
        event_bus.subscribe("subscription_updated", self.handle_subscription_update)
        event_bus.subscribe("subscription_canceled", self.handle_subscription_cancel)
        event_bus.subscribe("plugin_settings_updated", self.handle_plugin_settings_updated)
        logger.info("✅ Notification event handlers registered")

    def _load_config(self):
        """
        Load the notifications configuration from JSON file with caching
        """
        current_time = time.time()
        
        # Return cached config if still valid
        if self.config and (current_time - self.config_last_loaded) < NOTIFICATION_CACHE_TTL:
            return self.config
            
        config_path = self._config_root / "notifications_config.json"
        try:
            with open(config_path, "r") as f:
                self.config = json.load(f)
                self.config_last_loaded = current_time
                logger.info(f"Loaded notifications config with {len(self.config.get('categories', []))} categories")
                return self.config
        except FileNotFoundError:
            logger.error(f"Notifications config file not found at {config_path}")
            self.config = {"categories": [], "settings": {}}
            return self.config
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in notifications config file")
            self.config = {"categories": [], "settings": {}}
            return self.config

    @with_retry(max_retries=3, delay=1)
    async def get_user_notification_preferences(self, user_id):
        """
        Get notification preferences for a user with caching
        """
        try:
            user = await get_cached_document(
                users_collection,
                {"_id": ObjectId(user_id)},
                cache_key=f"user_notif_prefs:{user_id}"
            )
            
            if not user:
                logger.error(f"User {user_id} not found")
                return None
            
            # Get notification preferences from user document or create default
            notification_prefs = user.get("notification_preferences", {})
            
            # If no preferences exist, initialize with defaults
            if not notification_prefs:
                notification_prefs = await self._get_default_preferences()
                
            return notification_prefs
        except Exception as e:
            logger.error(f"Error getting notification preferences for user {user_id}: {e}")
            return None

    async def _get_default_preferences(self):
        """
        Create default notification preferences based on settings config
        """
        # Load settings config to get notification fields
        settings_config_path = self._config_root / "settings_config.json"
        try:
            with open(settings_config_path, "r") as f:
                settings_config = json.load(f)
        except Exception as e:
            logger.error(f"Error loading settings config: {e}")
            settings_config = {"profile_sections": []}
            
        # Ensure config is loaded
        if not self.config:
            self._load_config()
            
        # Get global default settings
        default_enabled = self.config.get("settings", {}).get("default_enabled", True)
        
        default_prefs = {}
        
        # Find notifications section
        notifications_section = next(
            (section for section in settings_config.get("profile_sections", []) 
            if section.get("id") == "notifications"), 
            None
        )
        
        if not notifications_section:
            return default_prefs
            
        # Get all notification fields (regular + plugin)
        notification_fields = notifications_section.get("fields", [])
        plugin_fields = notifications_section.get("plugin_notification_fields", [])
        
        # Combine all fields
        all_fields = notification_fields + plugin_fields
        
        # Create default preferences for each field
        for field in all_fields:
            field_id = field.get("id")
            if field_id and field.get("type") == "toggle":
                # Use field-specific default if available, otherwise use global default
                field_default = field.get("default_enabled", default_enabled)
                
                default_prefs[field_id] = {
                    "enabled": field_default,
                    "frequency": "immediate"  # Simplify by always using immediate
                }
            
        return default_prefs

    @with_retry(max_retries=3, delay=1)
    async def update_notification_preferences(self, user_id, preferences):
        """
        Update notification preferences for a user
        """
        try:
            # Load settings config to get valid notification fields
            settings_config_path = self._config_root / "settings_config.json"
            try:
                with open(settings_config_path, "r") as f:
                    settings_config = json.load(f)
            except Exception as e:
                logger.error(f"Error loading settings config: {e}")
                return False
                
            # Find notifications section
            notifications_section = next(
                (section for section in settings_config.get("profile_sections", []) 
                if section.get("id") == "notifications"), 
                None
            )
            
            if not notifications_section:
                logger.error("No notifications section found in settings config")
                return False
                
            # Get all notification fields (regular + plugin)
            notification_fields = notifications_section.get("fields", [])
            plugin_fields = notifications_section.get("plugin_notification_fields", [])
            
            # Get valid field IDs
            valid_field_ids = [
                field.get("id") 
                for field in (notification_fields + plugin_fields) 
                if field.get("id") and field.get("type") == "toggle"
            ]
            
            # Filter out invalid notification types
            valid_prefs = {}
            for pref_id, pref_data in preferences.items():
                if pref_id in valid_field_ids:
                    valid_prefs[pref_id] = {
                        "enabled": bool(pref_data.get("enabled", True)),
                        "frequency": "immediate"  # Simplify by always using immediate
                    }
            
            # Update preferences in the database
            result = await users_collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {"notification_preferences": valid_prefs}}
            )
            
            # Invalidate cache
            from core.config.database import db_cache
            db_cache.invalidate(f"user_notif_prefs:{user_id}")
            
            if result.modified_count == 0:
                logger.warning(f"No changes made to notification preferences for user {user_id}")
                
            # Publish event for notification preferences updated
            event_bus.publish("notification_preferences_updated", {"user_id": user_id})
            
            return valid_prefs
        except Exception as e:
            logger.error(f"Error updating notification preferences for user {user_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to update notification preferences: {str(e)}")

    async def get_notification_config(self, user_id=None, monetization_enabled=False):
        """
        Return the notification configuration for UI display,
        filtered by subscription access if monetization is enabled
        """
        # Ensure config is loaded
        if not self.config:
            self._load_config()
        
        # Load settings config to get notification fields
        settings_config_path = self._config_root / "settings_config.json"
        try:
            with open(settings_config_path, "r") as f:
                settings_config = json.load(f)
        except Exception as e:
            logger.error(f"Error loading settings config: {e}")
            return self.config
        
        # Find notifications section
        notifications_section = next(
            (section for section in settings_config.get("profile_sections", []) 
            if section.get("id") == "notifications"), 
            None
        )
        
        if not notifications_section:
            return self.config
        
        # Get all notification fields (regular + plugin)
        notification_fields = notifications_section.get("fields", [])
        plugin_fields = notifications_section.get("plugin_notification_fields", [])
        
        # Filter plugin fields by subscription access if needed
        if monetization_enabled and user_id:
            filtered_plugin_fields = []
            
            # Group plugin fields by plugin name to check access once per plugin
            plugin_groups = {}
            for field in plugin_fields:
                plugin_name = field.get("plugin")
                if plugin_name:
                    if plugin_name not in plugin_groups:
                        plugin_groups[plugin_name] = []
                    plugin_groups[plugin_name].append(field)
            
            # Check access for each plugin
            from core.subscription_manager import subscription_manager
            for plugin_name, fields in plugin_groups.items():
                has_access = await subscription_manager.is_plugin_accessible(user_id, plugin_name)
                if has_access:
                    filtered_plugin_fields.extend(fields)
            
            plugin_fields = filtered_plugin_fields
        
        # Create our dynamic notifications list based on settings_config.json
        notifications = []
        
        # Determine available channels based on hosting service
        available_channels = ["in_app"]
        if HOSTING_SERVICE:
            available_channels.append("email")
        
        # Add standard notification fields
        for field in notification_fields:
            if field.get("type") == "toggle" and field.get("id"):
                notification = {
                    "id": field.get("id"),
                    "category": field.get("category", "system"),
                    "name": field.get("label", field.get("id")),
                    "description": field.get("description", ""),
                    "default": True,
                    "channels": available_channels,
                    "frequencies": ["immediate"]
                }
                notifications.append(notification)
        
        # Add plugin notification fields
        for field in plugin_fields:
            if field.get("type") == "toggle" and field.get("id"):
                # Use channels defined in field, or fallback to available channels
                field_channels = field.get("channels", available_channels)
                
                # Ensure we only use channels that are actually available
                channels = [ch for ch in field_channels if ch in available_channels]
                
                notification = {
                    "id": field.get("id"),
                    "category": field.get("category", "plugins"),
                    "name": field.get("label", field.get("id")),
                    "description": field.get("description", ""),
                    "default": field.get("default_enabled", True),
                    "channels": channels,
                    "frequencies": ["immediate"],
                    "plugin": field.get("plugin")
                }
                notifications.append(notification)
        
        # Create the config to return
        config = {
            "categories": self.config.get("categories", []),
            "notifications": notifications,
            "email_service_enabled": HOSTING_SERVICE,
            "settings": self.config.get("settings", {})
        }
        
        return config

    # Helper method for async email sending
    async def _send_email_notification(self, user_id, notification_type, title, message):
        """
        Send email notification asynchronously without blocking the main flow
        Uses a semaphore to limit concurrent email sends
        """
        async with self._email_semaphore:
            try:
                user_data = await get_cached_document(
                    users_collection,
                    {"_id": ObjectId(user_id)},
                    cache_key=f"user_email:{user_id}"
                )
                
                if user_data and user_data.get("email"):
                    recipient = user_data["email"]
                    logger.info(f"Asynchronously sending email notification for {notification_type} to {recipient}")
                    await self.send_email(
                        recipient=recipient,
                        subject=title,
                        message=message,
                        notification_type=notification_type
                    )
            except Exception as e:
                logger.error(f"Failed to send async email notification: {e}")
                logger.error(traceback.format_exc())

    async def send_email(self, recipient, subject, message, notification_type=None):
        """
        Send an email using the configured email service
        """
        if not HOSTING_SERVICE:
            logger.info(f"Would send email to {recipient} but HOSTING_SERVICE is disabled")
            return False
            
        if not EMAIL_SERVICE_URL:
            logger.info(f"Would send email to {recipient} but EMAIL_SERVICE_URL is not configured")
            return False
            
        try:
            payload = {
                "recipient": recipient,
                "subject": subject, 
                "message": message,
                "notification_type": notification_type
            }
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {EMAIL_SERVICE_API_KEY}"
            }
            
            # Use aiohttp ClientSession with connection pooling
            async with aiohttp.ClientSession() as session:
                try:
                    # Add timeout for the request
                    async with session.post(
                        EMAIL_SERVICE_URL, 
                        json=payload, 
                        headers=headers,
                        timeout=10.0  # 10 second timeout
                    ) as response:
                        if response.status == 200:
                            logger.info(f"Email sent to {recipient} successfully")
                            return True
                        else:
                            response_text = await response.text()
                            logger.error(f"Failed to send email: Status {response.status} - {response_text}")
                            return False
                except aiohttp.ClientConnectorError as e:
                    logger.error(f"Connection error to email service: {e}")
                    return False
                except asyncio.TimeoutError:
                    logger.error(f"Timeout sending email to {recipient}")
                    return False
                        
        except Exception as e:
            logger.error(f"Error calling email service: {e}")
            logger.error(traceback.format_exc())
            return False

    async def create_notification(self, user_id, notification_type, title, message, metadata=None):
        """
        Create a new notification for a user, checking their preferences first
        Uses a background queue to avoid blocking the main request
        """
        try:
            # Add to queue for background processing
            notification = {
                "user_id": user_id,
                "type": notification_type,
                "title": title,
                "message": message,
                "metadata": metadata or {}
            }
            
            await self._notification_queue.put(notification)
            
            # Start background processing if not already running
            if not self._is_processing:
                self.start_background_processing()
                
            # Return placeholder notification ID
            return {"id": str(uuid.uuid4()), "queued": True}
                
        except Exception as e:
            logger.error(f"Error creating notification for user {user_id}: {e}")
            logger.error(traceback.format_exc())
            return None
    
    def start_background_processing(self):
        """
        Start background processing of notifications if not already running
        """
        if not self._is_processing:
            self._is_processing = True
            self._processing_task = asyncio.create_task(self._process_notification_queue())
            logger.info("Started background notification processing")
    
    async def stop_background_processing(self):
        """
        Stop background processing of notifications
        """
        if self._is_processing and self._processing_task:
            self._is_processing = False
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped background notification processing")
    
    async def _process_notification_queue(self):
        """
        Process notifications from the queue in the background
        """
        while self._is_processing:
            try:
                # Process up to NOTIFICATION_BATCH_SIZE notifications at once
                notifications = []
                for _ in range(min(NOTIFICATION_BATCH_SIZE, self._notification_queue.qsize() + 1)):
                    try:
                        notification = await asyncio.wait_for(self._notification_queue.get(), timeout=0.1)
                        notifications.append(notification)
                    except asyncio.TimeoutError:
                        break
                
                if not notifications:
                    # If queue is empty, wait a bit
                    await asyncio.sleep(1)
                    continue
                
                logger.info(f"Processing batch of {len(notifications)} notifications")
                
                # Group notifications by user for efficiency
                user_notifications = {}
                for notification in notifications:
                    user_id = notification["user_id"]
                    if user_id not in user_notifications:
                        user_notifications[user_id] = []
                    user_notifications[user_id].append(notification)
                
                # Process each user's notifications
                for user_id, user_notifs in user_notifications.items():
                    await self._process_user_notifications(user_id, user_notifs)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing notification queue: {e}")
                logger.error(traceback.format_exc())
                await asyncio.sleep(5)  # Wait before retrying on error
    
    async def _process_user_notifications(self, user_id, notifications):
        try:
            # Get user preferences
            user_prefs = await self.get_user_notification_preferences(user_id)
            if not user_prefs:
                user_prefs = await self._get_default_preferences()
            
            # Prepare lists for in-app and email notifications
            in_app_notifications = []
            email_notifications = []
            
            for notification in notifications:
                notification_type = notification["type"]
                is_enabled = user_prefs.get(notification_type, {}).get("enabled", True)
                
                # If specific type is not enabled, check a generic toggle for the plugin
                if not is_enabled:
                    plugin_name = self._get_plugin_from_notification_type(notification_type)
                    if plugin_name:
                        generic_plugin_notification = f"{plugin_name}_notifications"
                        is_enabled = user_prefs.get(generic_plugin_notification, {}).get("enabled", True)
                
                if is_enabled:
                    channels = await self._get_notification_channels(notification_type, user_id)
                    notification_id = str(uuid.uuid4())
                    notification_obj = {
                        "id": notification_id,
                        "user_id": user_id,
                        "type": notification_type,
                        "title": notification["title"],
                        "message": notification["message"],
                        "created_at": datetime.utcnow().isoformat(),
                        "read": False,
                        "metadata": notification["metadata"]
                    }
                    
                    if "in_app" in channels:
                        in_app_notifications.append(notification_obj)
                    if "email" in channels and HOSTING_SERVICE:
                        email_enabled = user_prefs.get("email_notifications", {}).get("enabled", True)
                        if email_enabled:
                            email_notifications.append(notification_obj)
            
            # Save in-app notifications (if any)
            if in_app_notifications:
                await self._save_in_app_notifications(user_id, in_app_notifications)
                # Push each in-app notification in real-time via WebSocket
                for notif in in_app_notifications:
                    await websocket_manager.send_to_user(user_id, {
                        "type": "notification",
                        "subtype": "new",
                        "data": notif
                    })
            
            # Send email notifications asynchronously
            for notif in email_notifications:
                asyncio.create_task(self._send_email_notification(
                    user_id=user_id,
                    notification_type=notif["type"],
                    title=notif["title"],
                    message=notif["message"]
                ))
                    
        except Exception as e:
            logger.error(f"Error processing notifications for user {user_id}: {e}")
            logger.error(traceback.format_exc())
    
    def _get_plugin_from_notification_type(self, notification_type):
        """
        Try to determine the plugin name from a notification type
        Uses caching for performance
        """
        # Check cache first
        if notification_type in self.notification_types_cache:
            return self.notification_types_cache[notification_type]
            
        # List of core (non-plugin) notification types
        core_notification_types = ["subscription_updates", "security_alerts", "email_notifications"]
        
        # If a core notification, return None
        if notification_type in core_notification_types:
            self.notification_types_cache[notification_type] = None
            return None
        
        # Try to determine plugin name from notification type
        notification_parts = notification_type.split('_')
        
        if len(notification_parts) >= 2:
            # Ensure config is loaded
            if not self.config:
                self._load_config()
                
            plugin_configs = self.config.get("plugins", {})
            
            # Try to find matching plugin by iteratively constructing plugin name
            for i in range(1, len(notification_parts)):
                potential_plugin = "_".join(notification_parts[:i])
                if potential_plugin in plugin_configs:
                    self.notification_types_cache[notification_type] = potential_plugin
                    return potential_plugin
            
            # If no matching plugin found, default to first part
            potential_plugin = notification_parts[0]
            self.notification_types_cache[notification_type] = potential_plugin
            return potential_plugin
        
        # No underscore, return None
        self.notification_types_cache[notification_type] = None
        return None
    
    async def _get_notification_channels(self, notification_type, user_id):
        """
        Determine which channels should be used for a notification type
        """
        # Default to in_app only
        allowed_channels = ["in_app"]
        
        # Check if this is a core notification
        core_notification_types = ["subscription_updates", "security_alerts", "email_notifications"]
        is_core_notification = notification_type in core_notification_types
        
        # Core notifications can use email if the email service is enabled
        if is_core_notification and HOSTING_SERVICE:
            allowed_channels.append("email")
            return allowed_channels
        
        # For plugin notifications, check settings_config
        plugin_name = self._get_plugin_from_notification_type(notification_type)
        if plugin_name:
            settings_config_path = self._config_root / "settings_config.json"
            try:
                with open(settings_config_path, "r") as f:
                    settings_config = json.load(f)
                    
                    # Find notifications section
                    notifications_section = next(
                        (section for section in settings_config.get("profile_sections", []) 
                        if section.get("id") == "notifications"),
                        None
                    )
                    
                    if notifications_section:
                        # Check for this specific notification type
                        plugin_fields = notifications_section.get("plugin_notification_fields", [])
                        notification_field = next(
                            (fld for fld in plugin_fields if fld.get("id") == notification_type),
                            None
                        )
                        
                        # If found, get channels
                        if notification_field and "channels" in notification_field:
                            channels = notification_field.get("channels", ["in_app"])
                            if "email" in channels and not HOSTING_SERVICE:
                                channels.remove("email")
                            return channels
            except Exception as e:
                logger.error(f"Error loading settings config: {e}")
        
        return allowed_channels

    @with_retry(max_retries=3, delay=1)
    async def _save_in_app_notifications(self, user_id, notifications):
        """
        Save in-app notifications to the database
        Ensures the user doesn't exceed MAX_NOTIFICATIONS_PER_USER
        """
        try:
            user_id_str = str(user_id) if isinstance(user_id, ObjectId) else user_id
            user_id_obj = ObjectId(user_id) if not isinstance(user_id, ObjectId) else user_id
            
            # Get current count of notifications for this user
            user = await users_collection.find_one(
                {"_id": user_id_obj},
                {"notifications": {"$slice": 1}, "_id": 1}  
            )
            
            if not user:
                logger.error(f"User {user_id_str} not found, can't save notifications")
                return False
            
            # Limit the number of notifications to add
            if len(notifications) > MAX_NOTIFICATIONS_PER_USER:
                logger.warning(f"Limiting batch to {MAX_NOTIFICATIONS_PER_USER} notifications")
                notifications = notifications[:MAX_NOTIFICATIONS_PER_USER]
            
            # Add new notifications and maintain max size
            update_result = await users_collection.update_one(
                {"_id": user_id_obj},
                {
                    "$push": {
                        "notifications": {
                            "$each": notifications,
                            "$sort": {"created_at": -1},
                            "$slice": -MAX_NOTIFICATIONS_PER_USER
                        }
                    }
                }
            )
            
            if update_result.modified_count > 0:
                logger.info(f"Saved {len(notifications)} in-app notifications for user {user_id_str}")
                return True
            else:
                logger.warning(f"No notifications were saved for user {user_id_str}")
                return False
                
        except Exception as e:
            logger.error(f"Error saving in-app notifications: {e}")
            logger.error(traceback.format_exc())
            raise
    
    async def create_bulk_notifications(self, user_ids, notification_type, title, message, metadata=None):
        """
        Queue notifications for multiple users at once
        """
        if not user_ids:
            return []
            
        try:
            notification_base = {
                "type": notification_type,
                "title": title,
                "message": message,
                "metadata": metadata or {}
            }
            
            notification_ids = []
            for user_id in user_ids:
                notification = notification_base.copy()
                notification["user_id"] = user_id
                await self._notification_queue.put(notification)
                notification_ids.append(str(uuid.uuid4()))
            
            if not self._is_processing:
                self.start_background_processing()
                
            return notification_ids
                
        except Exception as e:
            logger.error(f"Error creating bulk notifications: {e}")
            logger.error(traceback.format_exc())
            return []
    
    @with_retry(max_retries=3, delay=1)
    async def get_user_notifications(self, user_id, unread_only=False, limit=20, offset=0):
        """
        Get notifications for a user
        Optimized to only retrieve the needed notifications
        """
        try:
            user_id_obj = ObjectId(user_id) if not isinstance(user_id, ObjectId) else user_id
            
            query = {"_id": user_id_obj}
            projection = {"notifications": {"$slice": [offset, limit]}, "_id": 1}
            
            if unread_only:
                pipeline = [
                    {"$match": query},
                    {"$project": {
                        "_id": 1,
                        "notifications": {
                            "$filter": {
                                "input": "$notifications",
                                "as": "notif",
                                "cond": {"$eq": ["$notif.read", False]}
                            }
                        }
                    }},
                    {"$project": {
                        "_id": 1,
                        "notifications": {"$slice": ["$notifications", offset, limit]}
                    }}
                ]
                result = await users_collection.aggregate(pipeline).to_list(length=1)
                if not result:
                    return []
                return result[0].get("notifications", [])
            else:
                user = await users_collection.find_one(query, projection)
                if not user:
                    return []
                return user.get("notifications", [])
        except Exception as e:
            logger.error(f"Error getting notifications for user {user_id}: {e}")
            return []
    
    @with_retry(max_retries=3, delay=1)
    async def mark_notification_read(self, user_id, notification_id):
        """
        Mark a notification as read
        """
        try:
            result = await users_collection.update_one(
                {"_id": ObjectId(user_id), "notifications.id": notification_id},
                {"$set": {"notifications.$.read": True}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error marking notification as read for user {user_id}: {e}")
            return False
    
    @with_retry(max_retries=3, delay=1)
    async def mark_all_notifications_read(self, user_id):
        """
        Mark all notifications as read for a user
        """
        try:
            result = await users_collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {"notifications.$[].read": True}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error marking all notifications as read for user {user_id}: {e}")
            return False
    
    @with_retry(max_retries=3, delay=1)
    async def delete_notification(self, user_id, notification_id):
        """
        Delete a notification
        """
        try:
            result = await users_collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$pull": {"notifications": {"id": notification_id}}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error deleting notification for user {user_id}: {e}")
            return False

    # ------------------------------------------------------------------------
    #         🔽🔽🔽  HERE ARE THE CLASS-BASED EVENT HANDLERS  🔽🔽🔽
    # ------------------------------------------------------------------------

    # NO @on_event DECORATORS HERE.
    # Instead, the instance method is subscribed in register_event_handlers().
    async def handle_subscription_update(self, event_data):
        """
        Handle subscription update events
        """
        user_id = event_data.get("user_id")
        plan = event_data.get("plan")
        
        if user_id and plan:
            logger.info(f"Creating subscription_updates notification for user {user_id}")
            await self.create_notification(
                user_id=user_id,
                notification_type="subscription_updates",
                title="Subscription Updated",
                message=f"Your subscription has been updated to the {plan} plan.",
                metadata={"plan": plan}
            )

    async def handle_subscription_cancel(self, event_data):
        """
        Handle subscription cancellation events
        """
        user_id = event_data.get("user_id")
        
        if user_id:
            await self.create_notification(
                user_id=user_id,
                notification_type="subscription_updates",
                title="Subscription Cancelled",
                message="Your subscription has been cancelled. You'll have access until the end of your billing period.",
                metadata={"status": "cancelled"}
            )
    
    async def handle_plugin_settings_updated(self, event_data):
        """
        Handle plugin settings updated events
        """
        user_id = event_data.get("user_id")
        plugin_name = event_data.get("plugin")
        
        if user_id and plugin_name:
            # Create notification for plugin settings update
            await self.create_notification(
                user_id=user_id,
                notification_type=f"{plugin_name}_settings_updated",
                title=f"{plugin_name.replace('_', ' ').title()} Settings Updated",
                message=f"Your settings for {plugin_name.replace('_', ' ').title()} have been updated.",
                metadata={"plugin": plugin_name}
            )


# Initialize the notifications manager as a singleton
notifications_manager = NotificationsManager()

# Create indexes for the notifications
async def create_notification_indexes():
    """
    Create indexes for faster notification queries
    """
    try:
        # Create index on notifications.id for faster lookups
        await users_collection.create_index([("notifications.id", ASCENDING)])
        logger.info("✅ Created index on notifications.id")
        
        # Create index on notifications.read for faster unread queries
        await users_collection.create_index([("notifications.read", ASCENDING)])
        logger.info("✅ Created index on notifications.read")
        
        # Create index on notifications.created_at for sorting
        await users_collection.create_index([("notifications.created_at", ASCENDING)])
        logger.info("✅ Created index on notifications.created_at")
    except Exception as e:
        logger.error(f"❌ Error creating notification indexes: {e}")
