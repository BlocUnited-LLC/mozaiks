"""First-run bootstrap ritual for local Mozaiks workspaces.

This command is intentionally interactive by default to help first-time
developers create a working local setup with minimal decisions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from getpass import getpass
from pathlib import Path
from typing import Any

from mozaiksai.cli.generators import realm as realm_generator
from mozaiksai.cli.generators import theme as theme_generator
from mozaiksai.cli.paths import find_project_root


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "my-app"


def _parse_bool(value: object, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y", "on"):
        return True
    if text in ("false", "0", "no", "n", "off"):
        return False
    return default


def _infer_ws_url(api_url: str) -> str:
    text = api_url.strip()
    if text.startswith("https://"):
        return "wss://" + text[len("https://") :]
    if text.startswith("http://"):
        return "ws://" + text[len("http://") :]
    return "ws://localhost:8000"


def _prompt(label: str, default: str, *, secret: bool = False, non_interactive: bool = False) -> str:
    if non_interactive:
        return default

    suffix = f" [{default}]" if default else ""
    prompt = f"{label}{suffix}: "
    try:
        raw = getpass(prompt) if secret else input(prompt)
    except EOFError:
        return default
    value = raw.strip()
    return value if value else default


def _prompt_bool(
    label: str,
    default: bool,
    *,
    non_interactive: bool = False,
) -> bool:
    default_str = "Y/n" if default else "y/N"
    if non_interactive:
        return default

    while True:
        answer = input(f"{label} [{default_str}]: ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'.")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _ensure_env_file(root: Path) -> tuple[Path, bool]:
    env_path = root / ".env"
    if env_path.is_file():
        return env_path, False

    env_example = root / ".env.example"
    if not env_example.is_file():
        raise FileNotFoundError(f".env.example not found at {env_example}")

    shutil.copy2(env_example, env_path)
    return env_path, True


def _upsert_env(path: Path, key: str, value: str) -> None:
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()

    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replacement = f"{key}={value}"

    replaced = False
    updated: list[str] = []
    for line in lines:
        if pattern.match(line):
            updated.append(replacement)
            replaced = True
        else:
            updated.append(line)

    if not replaced:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append(replacement)

    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _build_app_config(existing: dict[str, Any], *, app_name: str, app_id: str, api_url: str, ws_url: str) -> dict[str, Any]:
    config = dict(existing)

    config["appName"] = app_name
    config["appId"] = app_id
    config["apiUrl"] = api_url
    config["wsUrl"] = ws_url

    auth = config.get("auth")
    if not isinstance(auth, dict):
        auth = {}
    auth["provider"] = auth.get("provider", "keycloak")

    keycloak = auth.get("keycloak")
    if not isinstance(keycloak, dict):
        keycloak = {}
    keycloak.setdefault("authority", "http://localhost:8080")
    keycloak.setdefault("realm", "mozaiks")
    keycloak.setdefault("clientId", "mozaiks-app")
    keycloak.setdefault("themeName", "mozaiks")
    auth["keycloak"] = keycloak
    config["auth"] = auth

    dev = config.get("dev")
    if not isinstance(dev, dict):
        dev = {}
    dev.setdefault("autoLogin", True)
    users = dev.get("users")
    if not isinstance(users, list) or not users:
        dev["users"] = [
            {
                "username": "dev",
                "password": "dev",
                "email": "dev@mozaiks.local",
                "firstName": "Dev",
                "lastName": "User",
                "roles": ["user", "admin"],
            }
        ]
    config["dev"] = dev

    return config


def _normalize_values(
    *,
    existing: dict[str, Any],
    app_name: str | None,
    app_id: str | None,
    api_url: str | None,
    ws_url: str | None,
    auth_enabled: bool | None,
) -> dict[str, Any]:
    resolved_app_name = str(app_name or existing.get("appName") or "My App").strip() or "My App"
    resolved_app_id = _slugify(str(app_id or existing.get("appId") or _slugify(resolved_app_name)))
    resolved_api_url = str(api_url or existing.get("apiUrl") or "http://localhost:8000").strip()
    resolved_ws_url = str(ws_url or existing.get("wsUrl") or _infer_ws_url(resolved_api_url)).strip()
    resolved_auth_enabled = _parse_bool(auth_enabled, default=True)
    return {
        "app_name": resolved_app_name,
        "app_id": resolved_app_id,
        "api_url": resolved_api_url,
        "ws_url": resolved_ws_url,
        "auth_enabled": resolved_auth_enabled,
    }


def _resolve_openai_api_key(root: Path, provided_key: str | None) -> str:
    candidate = (provided_key or "").strip()
    if candidate:
        return candidate

    env_key = os.getenv("OPENAI_API_KEY", "").strip()
    if env_key and env_key != "sk-...":
        return env_key

    env_path = root / ".env"
    env_values = _load_env(env_path)
    file_key = env_values.get("OPENAI_API_KEY", "").strip()
    if file_key and file_key != "sk-...":
        return file_key

    return ""


def _run_llm_ritual(
    *,
    root: Path,
    existing: dict[str, Any],
    openai_api_key: str | None,
    llm_model: str | None,
) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError(f"openai package unavailable: {exc}") from exc

    key = _resolve_openai_api_key(root, openai_api_key)
    if not key:
        key = _prompt(
            "OpenAI API key (required for --llm mode)",
            "",
            secret=True,
            non_interactive=False,
        )
    if not key:
        raise RuntimeError("No OpenAI API key available for --llm mode.")

    model = (llm_model or os.getenv("MOZAIKS_BOOTSTRAP_MODEL") or "gpt-4o-mini").strip()
    client = OpenAI(api_key=key)

    state = _normalize_values(
        existing=existing,
        app_name=None,
        app_id=None,
        api_url=None,
        ws_url=None,
        auth_enabled=True,
    )

    system_prompt = (
        "You are a setup assistant for Mozaiks. Ask exactly one concise question at a time "
        "to collect fields: app_name, app_id, api_url, ws_url, auth_enabled. "
        "Return only valid JSON with keys: done (boolean), question (string), config (object). "
        "Never include markdown."
    )

    transcript: list[dict[str, str]] = []
    user_reply = "Start the bootstrap ritual."

    for _ in range(12):
        payload = {
            "current_state": state,
            "required_fields": ["app_name", "app_id", "api_url", "ws_url", "auth_enabled"],
            "last_user_reply": user_reply,
        }
        messages = [{"role": "system", "content": system_prompt}, *transcript, {"role": "user", "content": json.dumps(payload)}]

        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=messages,
        )

        content = (response.choices[0].message.content or "{}").strip()
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"LLM response was not valid JSON: {exc}") from exc

        config = decoded.get("config", {})
        if isinstance(config, dict):
            state = _normalize_values(
                existing=existing,
                app_name=config.get("app_name") or state.get("app_name"),
                app_id=config.get("app_id") or state.get("app_id"),
                api_url=config.get("api_url") or state.get("api_url"),
                ws_url=config.get("ws_url") or state.get("ws_url"),
                auth_enabled=config.get("auth_enabled") if "auth_enabled" in config else state.get("auth_enabled"),
            )

        done = bool(decoded.get("done", False))
        question = str(decoded.get("question", "")).strip()
        transcript.append({"role": "assistant", "content": content})

        if done:
            break

        if not question:
            question = "What should we set next for your project?"
        print(f"LLM: {question}")
        user_reply = input("You: ").strip() or "Use default."
        transcript.append({"role": "user", "content": user_reply})

    state["openai_api_key"] = key
    return state


def run(
    *,
    root: Path | None = None,
    non_interactive: bool = False,
    app_name: str | None = None,
    app_id: str | None = None,
    api_url: str | None = None,
    ws_url: str | None = None,
    openai_api_key: str | None = None,
    auth_enabled: bool | None = None,
    skip_generate: bool = False,
    llm_mode: bool = False,
    llm_model: str | None = None,
) -> int:
    """Run the first-run bootstrap ritual."""
    root = root or find_project_root()
    app_json_path = root / "app" / "app.json"
    existing = _load_json(app_json_path)

    if llm_mode:
        if non_interactive:
            print("FAIL: --llm cannot be combined with --non-interactive.", file=sys.stderr)
            return 1
        try:
            llm_values = _run_llm_ritual(
                root=root,
                existing=existing,
                openai_api_key=openai_api_key,
                llm_model=llm_model,
            )
        except Exception as exc:
            print(f"FAIL: LLM bootstrap failed: {exc}", file=sys.stderr)
            return 1

        asked_app_name = app_name or str(llm_values.get("app_name", "My App"))
        asked_app_id = _slugify(app_id or str(llm_values.get("app_id", _slugify(asked_app_name))))
        asked_api_url = api_url or str(llm_values.get("api_url", "http://localhost:8000"))
        asked_ws_url = ws_url or str(llm_values.get("ws_url", _infer_ws_url(asked_api_url)))
        asked_auth_enabled = auth_enabled if auth_enabled is not None else _parse_bool(llm_values.get("auth_enabled"), True)
        provided_api_key = openai_api_key.strip() if openai_api_key else str(llm_values.get("openai_api_key", "")).strip()
    else:
        default_app_name = str(existing.get("appName", "My App"))
        asked_app_name = app_name or _prompt("App name", default_app_name, non_interactive=non_interactive)

        default_app_id = str(existing.get("appId", _slugify(asked_app_name)))
        asked_app_id = app_id or _prompt("App ID (slug)", default_app_id, non_interactive=non_interactive)
        asked_app_id = _slugify(asked_app_id)

        default_api_url = str(existing.get("apiUrl", "http://localhost:8000"))
        asked_api_url = api_url or _prompt("Backend API URL", default_api_url, non_interactive=non_interactive)

        default_ws_url = str(existing.get("wsUrl", _infer_ws_url(asked_api_url)))
        asked_ws_url = ws_url or _prompt("Backend WebSocket URL", default_ws_url, non_interactive=non_interactive)

        if auth_enabled is None:
            asked_auth_enabled = _prompt_bool(
                "Enable auth (Keycloak) by default",
                True,
                non_interactive=non_interactive,
            )
        else:
            asked_auth_enabled = auth_enabled

        if openai_api_key is None:
            provided_api_key = _prompt(
                "OpenAI API key (leave blank to keep current value)",
                "",
                secret=True,
                non_interactive=non_interactive,
            )
        else:
            provided_api_key = openai_api_key.strip()

    updated_config = _build_app_config(
        existing,
        app_name=asked_app_name,
        app_id=asked_app_id,
        api_url=asked_api_url,
        ws_url=asked_ws_url,
    )
    _write_json(app_json_path, updated_config)
    print(f"OK: Updated {app_json_path.relative_to(root)}")

    try:
        env_path, created = _ensure_env_file(root)
    except FileNotFoundError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if created:
        print("OK: Created .env from .env.example")
    else:
        print("OK: Reusing existing .env")

    _upsert_env(env_path, "AUTH_ENABLED", "true" if asked_auth_enabled else "false")
    if provided_api_key:
        _upsert_env(env_path, "OPENAI_API_KEY", provided_api_key)

    if skip_generate:
        print("INFO: Skipped artifact generation (--skip-generate).")
    else:
        print("Syncing generated artifacts...")
        rc = realm_generator.run(root=root, dry_run=False)
        if rc != 0:
            return rc
        rc = theme_generator.run(root=root, dry_run=False)
        if rc != 0:
            return rc

    print("")
    print("Bootstrap complete. Next steps:")
    print("  1) python -m mozaiksai.cli doctor")
    print("  2) .\\start-dev.ps1 -Mode docker -StartFrontend")
    return 0
