# ==============================================================================
# FILE: mozaiksai/core/core_config.py
# DESCRIPTION: Lazy MongoDB and app-backend configuration using shared secret policy.
# ==============================================================================
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

from logs.logging_config import get_core_logger

load_dotenv()
logger = get_core_logger("core_config")
_mongo_client: AsyncIOMotorClient | None = None
_mongo_client_conn_str: str | None = None

# -----------------------------
# MongoDB Connection
# -----------------------------
def get_mongo_client() -> AsyncIOMotorClient:
    """Get MongoDB client using the selected app's MONGO_URI secret policy.

    Avoids defaulting to localhost, to prevent accidental local fallbacks.
    """
    # Keep configuration imports independent of app-loader initialization.
    from mozaiksai.core.secrets import resolve_secret

    global _mongo_client, _mongo_client_conn_str
    conn_str = resolve_secret("MONGO_URI")
    if _mongo_client is not None and _mongo_client_conn_str == conn_str:
        return _mongo_client
    if _mongo_client is not None:
        _mongo_client.close()
    _mongo_client = AsyncIOMotorClient(conn_str)
    _mongo_client_conn_str = conn_str
    return _mongo_client


def close_mongo_client() -> None:
    """Close the process-scoped Mongo client and clear the cached handle."""
    global _mongo_client, _mongo_client_conn_str
    if _mongo_client is not None:
        _mongo_client.close()
    _mongo_client = None
    _mongo_client_conn_str = None


# MongoDB Collections are obtained via PersistenceManager to avoid early initialization

# -----------------------------
# App/App ID resolution (for UI tools and persistence)
# -----------------------------
def get_app_id_from_chat_or_context(chat_id: str | None = None) -> str | None:
    """Best-effort app_id lookup for a chat_id.

    Used by UI-tool persistence helpers that do not have direct access to the
    active WebSocket connection metadata.
    """

    if not chat_id:
        return None

    try:
        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = getattr(SimpleTransport, "_instance", None)
        if not transport or not hasattr(transport, "connections"):
            return None

        conn = transport.connections.get(chat_id)
        if not isinstance(conn, dict):
            return None

        raw_app_id = conn.get("app_id")
        if raw_app_id is None:
            return None
        if isinstance(raw_app_id, str):
            trimmed = raw_app_id.strip()
            return trimmed or None
        return str(raw_app_id) or None
    except Exception:
        return None


# -----------------------------
# App backend integration
# -----------------------------
MOZAIKS_BACKEND_URL = os.getenv("MOZAIKS_BACKEND_URL", "http://localhost:8000").strip().rstrip("/")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "").strip()

__all__ = [
    "get_mongo_client",
    "close_mongo_client",
    "get_app_id_from_chat_or_context",
    "MOZAIKS_BACKEND_URL",
    "INTERNAL_API_KEY",
]

