# ==============================================================================
# FILE: core\core_config.py
# DESCRIPTION: Configuration for Azure Key Vault, MongoDB, LLMs, and Tokens API
# NOTES: Avoid module-level cloud calls; build credentials lazily and prefer
#        environment variables to keep local/dev robust.
# ==============================================================================
import os
from dotenv import load_dotenv
from typing import Optional, Dict, Any
from logs.logging_config import get_core_logger

# Azure SDK imports are kept, but we won't construct credentials at import time
from azure.identity import DefaultAzureCredential
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()
logger = get_core_logger("core_config")

# -----------------------------
# Azure Key Vault utilities (lazy, optional)
# -----------------------------
def _get_kv_uri() -> Optional[str]:
    name = os.getenv("AZURE_KEY_VAULT_NAME")
    if name:
        return f"https://{name.strip()}.vault.azure.net/"
    return None

def _build_secret_client() -> Optional[Any]:
    """Create a SecretClient lazily if Key Vault is configured; otherwise return None.

    Note: We import SecretClient inside the function to avoid module import failures
    when azure-keyvault-secrets isn't installed in non-KV environments.
    """
    kv_uri = _get_kv_uri()
    if not kv_uri:
        return None
    try:
        from azure.keyvault.secrets import SecretClient  # type: ignore
    except Exception:
        return None
    try:
        cred = DefaultAzureCredential()
        return SecretClient(vault_url=kv_uri, credential=cred)
    except Exception:
        return None


def get_secret(name: str) -> str:
    """Get a secret value from environment or Key Vault.

    Order:
    1) Environment variable by exact uppercased name (e.g., OpenAIApiKey -> OPENAIAPIKEY)
    2) Common env aliases for well-known secrets (e.g., MongoURI -> MONGO_URI | MONGODB_URI | MONGO_URL)
    3) Azure Key Vault secret by the provided name, if KV is configured
    """
    # 1) Direct env by uppercased name
    env_key = name.upper()
    env_val = os.getenv(env_key)
    if env_val:
        return env_val

    # 2) Common aliases for Mongo
    if name in ("MongoURI", "MONGO_URI", "MONGODB_URI", "MONGO_URL"):
        for alias in ("MONGO_URI", "MONGODB_URI", "MONGO_URL"):
            val = os.getenv(alias)
            if val:
                return val

    # 3) Azure Key Vault fallback
    client = _build_secret_client()
    if client is not None:
        try:
            secret = client.get_secret(name)
            if secret and getattr(secret, "value", None):
                return secret.value  # type: ignore[attr-defined]
        except Exception:
            pass

    raise ValueError(f"Secret '{name}' not found in environment or Key Vault")

# -----------------------------
# MongoDB Connection
# -----------------------------
def get_mongo_client() -> AsyncIOMotorClient:
    """Get MongoDB client using MONGO_URI env or Key Vault secret 'MongoURI'.

    Avoids defaulting to localhost, to prevent accidental local fallbacks.
    """
    conn_str = os.getenv("MONGO_URI")
    if not conn_str:
        # Fall back to KV only if env is missing
        conn_str = get_secret("MongoURI")
    if not conn_str:
        raise ValueError("MONGO_URI is not configured")
    return AsyncIOMotorClient(conn_str)


# MongoDB Collections are obtained via PersistenceManager to avoid early initialization

# -----------------------------
# App/App ID resolution (for UI tools and persistence)
# -----------------------------
def get_app_id_from_chat_or_context(chat_id: Optional[str] = None) -> Optional[str]:
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
# Mozaiks Backend integration (GitHub deploy pipeline)
# -----------------------------
MOZAIKS_BACKEND_URL = os.getenv("MOZAIKS_BACKEND_URL", "https://api.mozaiks.ai").strip().rstrip("/")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "").strip()

__all__ = [
    "get_secret",
    "get_mongo_client",
    "get_app_id_from_chat_or_context",
    "MOZAIKS_BACKEND_URL",
    "INTERNAL_API_KEY",
]

