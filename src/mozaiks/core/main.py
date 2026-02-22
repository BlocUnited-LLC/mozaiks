from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("mozaiks.core.api.app:create_app", factory=True, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
