# CLI Reference

The `mozaiks` CLI handles local workspace management — creating workspaces,
starting services, and checking status. Once things are running, use Studio for
everything else.

## Quickstart

The single command most users need:

=== "Windows"

    ```powershell
    python -m mozaiks quickstart --dir .\my-workspace
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks quickstart --dir ./my-workspace
    ```

Scaffolds the workspace, starts the backend and frontend, and opens Studio at
`http://localhost:3000`.

## All Commands

### `quickstart`

Create a new workspace, start all services, and open Studio.

=== "Windows"

    ```powershell
    python -m mozaiks quickstart --dir .\my-workspace
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks quickstart --dir ./my-workspace
    ```

### `studio`

Start Studio for an existing workspace.

=== "Windows"

    ```powershell
    python -m mozaiks studio --dir .\my-workspace --open
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks studio --dir ./my-workspace --open
    ```

**Options:**

| Flag | Effect |
| --- | --- |
| `--open` | Open Studio in the browser after starting |
| `--json` | Print workspace status as JSON and exit |
| `--backend-port N` | Use a custom backend port (default: `8000`) |
| `--frontend-port N` | Use a custom frontend port (default: `3000`) |

### `onboard`

Onboard an existing app directory into a workspace.

=== "Windows"

    ```powershell
    python -m mozaiks onboard --dir .\my-workspace
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks onboard --dir ./my-workspace
    ```

### `serve`

Start a host directly without Studio. Useful when you only need the runtime.

```bash
mozaiks serve ./my-app                            # platform host on :8000
mozaiks serve ./my-app --host studio              # Studio host on :8000
mozaiks serve ./my-app --host platform --reload   # with live reload
mozaiks serve ./my-app --host platform --port 8001
```

## Quick Reference Table

| Command | What it does |
| --- | --- |
| `python -m mozaiks quickstart --dir <path>` | Create workspace, start services, open Studio |
| `python -m mozaiks studio --dir <path> --open` | Start services for an existing workspace |
| `python -m mozaiks studio --dir <path> --json` | Print workspace status as JSON |
| `python -m mozaiks onboard --dir <path>` | Onboard an existing app into a workspace |
| `mozaiks serve <path>` | Start the platform host |
| `mozaiks serve <path> --host studio` | Start the Studio host |

## Troubleshooting

??? "Port already in use"

    === "Windows"

        ```powershell
        python -m mozaiks studio --dir .\my-workspace --open --backend-port 8001 --frontend-port 3001
        ```

    === "macOS / Linux"

        ```bash
        python -m mozaiks studio --dir ./my-workspace --open --backend-port 8001 --frontend-port 3001
        ```

??? "`mozaiks` command not found"
    Use `python -m mozaiks` instead. The `mozaiks` shortcut requires the Python
    scripts directory to be on PATH, which some systems do not configure automatically.

---

## Development Commands

For contributors working from a repo checkout:

| Command | What it does |
| --- | --- |
| `ruff check .` | Lint the codebase |
| `ruff check --fix .` | Lint and auto-fix |
| `pytest` | Run all tests |
| `pytest tests/test_foo.py` | Run a single test file |
| `pytest tests/test_foo.py::test_bar` | Run a single test |

For full contributor setup see [Local Setup](local-setup.md).
