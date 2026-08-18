# Modules Stub

Add deterministic capabilities here when the app actually needs them.

Each module lives under `app/modules/<name>/` and typically includes:

- `module.yaml`
- `contracts/events.yaml`
- `contracts/reactions.yaml`
- `contracts/settings.yaml`
- `contracts/notifications.yaml`
- `contracts/admin.yaml`
- `backend/handler.py`

Do not create modules until you know the real CRUD or action surface.
