# ==============================================================================
# FILE: mozaikscore/core/state_manager.py
# DESCRIPTION: In-memory key/value store with optional TTL.
#              Thread-safe.  No external dependencies.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/state_manager.py
# ==============================================================================
import threading
import logging
import time

logger = logging.getLogger("mozaikscore.state_manager")


class StateManager:
    def __init__(self):
        self.state: dict = {}
        self._lock = threading.Lock()

    def set(self, key: str, value, expire_in: int | None = None):
        """Store a key-value pair.  Optional *expire_in* in seconds."""
        with self._lock:
            self.state[key] = {
                "value": value,
                "expires_at": (time.time() + expire_in) if expire_in else None,
            }
            logger.debug("State set: '%s' (expires_in=%s)", key, expire_in)

    def get(self, key: str):
        """Retrieve a value.  Returns ``None`` if missing or expired."""
        with self._lock:
            entry = self.state.get(key)
            if not entry:
                return None
            if entry["expires_at"] and time.time() > entry["expires_at"]:
                del self.state[key]
                logger.debug("Expired state key removed: '%s'", key)
                return None
            return entry["value"]

    def delete(self, key: str):
        with self._lock:
            if key in self.state:
                del self.state[key]
                logger.debug("State key removed: '%s'", key)

    def clear(self):
        with self._lock:
            self.state.clear()
            logger.debug("All state cleared")


# Singleton
state_manager = StateManager()
