#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="./my-first-mozaiks-app"
preset="chat"
name=""
journey=""
goal=""
provider=""
model=""
backend_port="8000"
frontend_port="3000"
no_browser="false"
no_launch="false"
skip_install="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace)
      workspace="$2"
      shift 2
      ;;
    --preset)
      preset="$2"
      shift 2
      ;;
    --name)
      name="$2"
      shift 2
      ;;
    --journey)
      journey="$2"
      shift 2
      ;;
    --goal)
      goal="$2"
      shift 2
      ;;
    --provider)
      provider="$2"
      shift 2
      ;;
    --model)
      model="$2"
      shift 2
      ;;
    --backend-port)
      backend_port="$2"
      shift 2
      ;;
    --frontend-port)
      frontend_port="$2"
      shift 2
      ;;
    --no-browser)
      no_browser="true"
      shift
      ;;
    --no-launch)
      no_launch="true"
      shift
      ;;
    --skip-install)
      skip_install="true"
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/bootstrap-builder.sh [options]

Options:
  --workspace <path>       Workspace to create/configure (default: ./my-first-mozaiks-app)
  --preset <preset>        Scaffold preset when missing (default: chat)
  --name <name>            App name to store in the scaffold
  --journey <journey>      greenfield_app or brownfield_app
  --goal <text>            Initial Console goal
  --provider <provider>    anthropic, openai, local, or other
  --model <model>          Default model name
  --backend-port <port>    Backend port (default: 8000)
  --frontend-port <port>   Frontend port (default: 3000)
  --no-browser             Launch the Console without opening the browser
  --no-launch              Stop after venv/install bootstrap
  --skip-install           Skip pip install -e .
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -x "$repo_root/.venv/bin/python" ]]; then
  bootstrap_python="$repo_root/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  bootstrap_python="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  bootstrap_python="$(command -v python)"
else
  echo "Python 3.11+ is required. Install Python and rerun this script." >&2
  exit 1
fi

echo "Mozaiks builder bootstrap"
echo "Repo:      $repo_root"
echo "Workspace: $workspace"
echo

cd "$repo_root"

if [[ ! -x "$repo_root/.venv/bin/python" ]]; then
  echo "[1/3] Creating .venv..."
  "$bootstrap_python" -m venv .venv
else
  echo "[1/3] Reusing existing .venv"
fi

venv_python="$repo_root/.venv/bin/python"

if [[ "$skip_install" != "true" ]]; then
  echo "[2/3] Installing Mozaiks in editable mode..."
  "$venv_python" -m pip install -e .
else
  echo "[2/3] Skipping editable install"
fi

quickstart_args=(
  -m
  mozaiks_cli.main
  quickstart
  --dir
  "$workspace"
  --preset
  "$preset"
  --backend-port
  "$backend_port"
  --frontend-port
  "$frontend_port"
)

if [[ -n "$name" ]]; then
  quickstart_args+=(--name "$name")
fi
if [[ -n "$journey" ]]; then
  quickstart_args+=(--journey "$journey")
fi
if [[ -n "$goal" ]]; then
  quickstart_args+=(--goal "$goal")
fi
if [[ -n "$provider" ]]; then
  quickstart_args+=(--provider "$provider")
fi
if [[ -n "$model" ]]; then
  quickstart_args+=(--model "$model")
fi
if [[ "$no_browser" == "true" ]]; then
  quickstart_args+=(--no-browser)
fi

if [[ "$no_launch" == "true" ]]; then
  echo "[3/3] Bootstrap complete; Studio launch skipped"
  echo
  echo "Next command:"
  printf '  %q ' "$venv_python" "${quickstart_args[@]}"
  echo
  exit 0
fi

echo "[3/3] Launching Mozaiks Studio..."
"$venv_python" "${quickstart_args[@]}"
