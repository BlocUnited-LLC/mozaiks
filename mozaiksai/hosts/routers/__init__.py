"""Platform host router modules — extracted from hosts/platform.py.

Each module exposes a FastAPI APIRouter that is included in the platform app
via app.include_router(). Helpers and models local to a router group live in
the same file; shared helpers that span multiple groups stay in platform.py
until a future extraction makes them worth separating.
"""
