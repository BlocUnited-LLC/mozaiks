from __future__ import annotations

import json

from mozaiksai.core.app_context import detect_frameworks_from_file_map


def test_detect_frameworks_from_next_react_repo() -> None:
    result = detect_frameworks_from_file_map(
        {
            "package.json": json.dumps(
                {
                    "scripts": {
                        "build": "next build",
                        "lint": "next lint",
                        "test": "vitest run",
                    },
                    "dependencies": {
                        "next": "^15.0.0",
                        "react": "^19.0.0",
                    },
                    "devDependencies": {
                        "vite": "^6.0.0",
                    },
                }
            ),
            "app/page.tsx": "export default function Home() { return <main /> }",
            "src/App.tsx": "export default function App() { return null }",
        }
    )

    framework_ids = {framework.framework_id for framework in result.frameworks}
    commands = {command.command for command in result.validation_commands}

    assert result.primary_framework_id == "nextjs"
    assert {"nextjs", "react", "vite", "node"}.issubset(framework_ids)
    assert "typescript" in result.languages
    assert "npm run build" in commands
    assert "npm run lint" in commands
    assert any(entry.path == "app/page.tsx" for entry in result.entrypoints)


def test_detect_frameworks_from_mozaiks_app_and_fastapi() -> None:
    result = detect_frameworks_from_file_map(
        {
            "app/app.json": '{"app_id": "ops"}',
            "app/modules/billing/module.yaml": "id: billing",
            "backend/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            "pyproject.toml": "[project]\ndependencies = ['fastapi']\n",
        }
    )

    framework_ids = {framework.framework_id for framework in result.frameworks}

    assert result.primary_framework_id == "mozaiks_app"
    assert {"mozaiks_app", "fastapi", "python"}.issubset(framework_ids)
    assert any(entry.framework_id == "fastapi" and entry.path == "backend/main.py" for entry in result.entrypoints)
    assert any(command.command == "python -m pytest" for command in result.validation_commands)
