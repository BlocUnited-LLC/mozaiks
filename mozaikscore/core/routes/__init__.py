# mozaikscore.core.routes — API route modules

from .admin_users import router as admin_users_router
from .notifications import router as notifications_router
from .notifications_admin import router as notifications_admin_router
from .analytics import router as analytics_router
from .status import router as status_router
from .app_metadata import router as app_metadata_router
from .push_subscriptions import router as push_subscriptions_router
from .events import router as events_router
from .subscription_sync import router as subscription_sync_router
from .theme import router as theme_router
from .settings import router as settings_router
from .profile import router as profile_router
from .modules import router as modules_router
from .subscriptions import router as subscriptions_router

__all__ = [
    "admin_users_router",
    "notifications_router",
    "notifications_admin_router",
    "analytics_router",
    "status_router",
    "app_metadata_router",
    "push_subscriptions_router",
    "events_router",
    "subscription_sync_router",
    "theme_router",
    "settings_router",
    "profile_router",
    "modules_router",
    "subscriptions_router",
]
