"""
mozaiks gen - Generate workflows or apps using AI.

This is the "quick gen" mode that skips the interview process.
User provides a descriptive prompt and the generator agents create
the workflow/app files directly.

Usage:
    mozaiks gen workflow --prompt "description of what you want"
    mozaiks gen app --prompt "description of your app"
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Dict, Any

import yaml

# Rich for nice CLI output
try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.panel import Panel
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = Console() if RICH_AVAILABLE else None


def _print(msg: str, style: str = None):
    """Print with optional rich styling."""
    if console and style:
        console.print(msg, style=style)
    else:
        print(msg)


def _print_error(msg: str):
    _print(f"Error: {msg}", style="bold red")


def _print_success(msg: str):
    _print(f"  {msg}", style="bold green")


def _print_info(msg: str):
    _print(f"  {msg}", style="dim")


def _check_api_key() -> bool:
    """Check if an LLM API key is configured."""
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY"):
        if os.environ.get(key):
            return True
    return False


def _find_generator_source() -> Optional[Path]:
    """Locate the AgentGenerator workflow source directory."""
    candidates = [
        # Inside the mozaiks repo (submodule / subtree copy)
        Path(__file__).parents[2] / "mozaiks-platform" / "app" / "workflows",
        # Sibling repo on disk
        Path(__file__).parents[3] / "mozaiks-platform" / "app" / "workflows",
        # Local platform directory
        Path(__file__).parents[2] / "platform" / "workflows",
        # Env override
        Path(os.environ.get("MOZAIKS_WORKFLOWS_PATH", ""))
        if os.environ.get("MOZAIKS_WORKFLOWS_PATH") else None,
    ]
    for p in candidates:
        if p and p.exists() and (p / "AgentGenerator").exists():
            return p
    return None


# ── Schema adaptation ──────────────────────────────────────────────
# Platform workflow YAMLs may use field names / extra fields that
# differ from the runtime's strict Pydantic contracts.  We normalise
# on-the-fly so the runtime can consume them.

_ORCHESTRATOR_FIELD_RENAMES = {
    "startup_mode": "workflow_startup_mode",
}
_ORCHESTRATOR_DROP_FIELDS = {"runtime_extensions"}

# Fields that need to be stripped from nested list items in specific files
_LIST_ITEM_DROP_FIELDS = {
    "handoffs.yaml": {
        "handoff_rules": {"description"},
    },
    "tools.yaml": {
        "lifecycle_tools": {"tool_type", "auto_tool_call"},
    },
}


def _adapt_orchestrator(data: dict) -> dict:
    """Return a copy of *data* adjusted to match the runtime schema."""
    out = {}
    for key, value in data.items():
        if key in _ORCHESTRATOR_DROP_FIELDS:
            continue
        out_key = _ORCHESTRATOR_FIELD_RENAMES.get(key, key)
        out[out_key] = value
    return out


def _adapt_yaml_file(filename: str, data: dict) -> dict:
    """Strip extra fields from YAML data based on known schema gaps."""
    # Strip extra fields from list items
    drop_rules = _LIST_ITEM_DROP_FIELDS.get(filename)
    if drop_rules:
        data = dict(data)
        for list_key, fields_to_drop in drop_rules.items():
            items = data.get(list_key)
            if isinstance(items, list):
                data[list_key] = [
                    {k: v for k, v in item.items() if k not in fields_to_drop}
                    if isinstance(item, dict) else item
                    for item in items
                ]

    # context_variables.yaml: convert list format → dict format
    if filename == "context_variables.yaml":
        data = _adapt_context_variables(data)

    # structured_outputs.yaml: unwrap nested key
    if filename == "structured_outputs.yaml":
        data = _adapt_structured_outputs(data)

    return data


def _adapt_context_variables(data: dict) -> dict:
    """Convert platform list-based context_variables to runtime dict format.

    Platform format:
        definitions:
          - name: foo
            type: str
            source: {origin: runtime, default: "bar"}
        agents:
          - agent_name: MyAgent
            variables: [foo, bar]

    Runtime format:
        definitions:
          foo:
            type: str
            source: {origin: runtime, default: "bar"}
        agents:
          MyAgent:
            variables: [foo, bar]
    """
    out = dict(data)

    # Fields that the runtime's ContextVariableSourceSpec does not accept
    _SOURCE_DROP = {"db_type", "transitions", "origin"}

    # definitions: list[{name, ...}] → dict[name, {...}]
    defs = out.get("definitions")
    if isinstance(defs, list):
        definitions_dict = {}
        for item in defs:
            if isinstance(item, dict):
                item = dict(item)
                name = item.pop("name", None)
                if name:
                    # Strip unsupported source fields
                    src = item.get("source")
                    if isinstance(src, dict):
                        item["source"] = {k: v for k, v in src.items() if k not in _SOURCE_DROP}
                    definitions_dict[name] = item
        out["definitions"] = definitions_dict
    elif isinstance(defs, dict):
        # Already dict format, just strip extra source fields
        for _name, defn in defs.items():
            if isinstance(defn, dict):
                src = defn.get("source")
                if isinstance(src, dict):
                    defn["source"] = {k: v for k, v in src.items() if k not in _SOURCE_DROP}

    # agents: list[{agent_name, ...}] → dict[agent_name, {...}]
    agents = out.get("agents")
    if isinstance(agents, list):
        agents_dict = {}
        for item in agents:
            if isinstance(item, dict):
                item = dict(item)
                agent_name = item.pop("agent_name", None)
                if agent_name:
                    agents_dict[agent_name] = item
        out["agents"] = agents_dict

    return out


def _adapt_structured_outputs(data: dict) -> dict:
    """Unwrap platform's nested structured_outputs key and strip extra fields.

    Platform format has extra keys (description, required) on model and
    field specs that the runtime's strict contracts reject.
    """
    nested = data.get("structured_outputs")
    if isinstance(nested, dict):
        data = nested

    # The runtime's StructuredOutputModelSpec only accepts: fields
    # The runtime's StructuredOutputFieldSpec only accepts: type, description, items, enum
    # But the platform adds: required, description (on models)
    _MODEL_DROP = {"description", "required"}
    _FIELD_DROP = {"required"}

    models = data.get("models")
    if isinstance(models, dict):
        cleaned_models = {}
        for model_name, model_spec in models.items():
            if not isinstance(model_spec, dict):
                cleaned_models[model_name] = model_spec
                continue
            cleaned = {k: v for k, v in model_spec.items() if k not in _MODEL_DROP}
            # Also strip from nested field specs
            fields = cleaned.get("fields")
            if isinstance(fields, dict):
                cleaned_fields = {}
                for field_name, field_spec in fields.items():
                    if isinstance(field_spec, dict):
                        cleaned_fields[field_name] = {k: v for k, v in field_spec.items() if k not in _FIELD_DROP}
                    else:
                        cleaned_fields[field_name] = field_spec
                cleaned["fields"] = cleaned_fields
            cleaned_models[model_name] = cleaned
        data = dict(data)
        data["models"] = cleaned_models

    return data


def _adapt_python_tool(content: str) -> str:
    """Fix Python tool files for AG2 compatibility.

    AG2 requires all function parameters to have type annotations.
    This patches common patterns like **runtime to **runtime: Any.

    ONLY modifies function DEFINITIONS (def/async def), NOT function calls.
    """
    import re

    needs_any_import = False

    # Strategy: Find patterns that look like **kwargs in function parameters
    # Key insight: In function definitions, **kwargs appears:
    # 1. At the end of a line followed by newline then ) on next line
    # 2. Before ) or , on the same line
    # 3. NOT inside function calls (where it's **dict_var and no annotation is valid)

    # Pattern to find function definitions with untyped **kwargs
    # Matches: **varname that is NOT followed by : (type annotation)
    # And is followed by either:
    #   - whitespace and newline (with ) on a later line)
    #   - ) or , directly

    # Multi-line pattern that matches **varname at end of line within function def
    # This is the tricky case: "    **runtime\n) -> ReturnType:"
    pattern1 = re.compile(
        r'(\*\*\w+)(\s*\n\s*\)\s*(?:->|:))',
        re.MULTILINE
    )

    # Single-line pattern: **varname) or **varname,
    pattern2 = re.compile(r'(\*\*\w+)(\s*[,)])')

    def add_type_if_missing(match, pattern_num):
        nonlocal needs_any_import
        kwargs_part = match.group(1)  # e.g., "**runtime"
        suffix = match.group(2)       # e.g., "\n) ->" or ")" or ","

        # Check if it already has a type annotation
        if re.search(r'\*\*\w+\s*:', kwargs_part):
            return match.group(0)  # Already annotated

        # Only add annotation if this looks like a function parameter
        # (not a dict unpacking in a function call)
        needs_any_import = True
        return kwargs_part + ': Any' + suffix

    # Apply pattern1 first (multi-line case)
    content = pattern1.sub(lambda m: add_type_if_missing(m, 1), content)

    # Now check if pattern2 needs to be applied
    # But be careful: we only want to modify function definitions, not calls
    # A heuristic: if the line contains "def " before the **, it's a definition
    # Or if the line is indented and looks like a parameter list item

    lines = content.split('\n')
    new_lines = []
    in_func_def = False
    paren_depth = 0

    for line in lines:
        stripped = line.strip()

        # Track when we enter/exit function definitions
        if re.match(r'^(async\s+)?def\s+\w+', stripped):
            in_func_def = True
            paren_depth = 0

        if in_func_def:
            # Simple paren tracking (not perfect but good enough)
            paren_depth += line.count('(') - line.count(')')

            # Look for untyped **kwargs in this line
            if '**' in line and not re.search(r'\*\*\w+\s*:', line):
                # Check if it's **varname followed by ) or , on same line
                if re.search(r'\*\*\w+\s*[,)]', line):
                    line = re.sub(r'(\*\*\w+)(\s*[,)])', r'\1: Any\2', line)
                    needs_any_import = True

            # Exit function def when we close all parens and see :
            if paren_depth <= 0 and ':' in line:
                in_func_def = False

        new_lines.append(line)

    content = '\n'.join(new_lines)

    # Add 'from typing import Any' if needed and not present
    if needs_any_import:
        if 'from typing import' in content:
            # Check if Any is already imported
            if not re.search(r'from typing import[^#\n]*\bAny\b', content):
                content = re.sub(
                    r'(from typing import )([^\n]+)',
                    r'\1Any, \2',
                    content,
                    count=1
                )
        else:
            # Add import at top after any existing imports
            lines = content.split('\n')
            last_import = -1
            for idx, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('import ') or stripped.startswith('from '):
                    last_import = idx
            if last_import >= 0:
                lines.insert(last_import + 1, 'from typing import Any')
            else:
                # No imports found, add at top after any docstrings/comments
                insert_at = 0
                for idx, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''") or stripped == '':
                        continue
                    insert_at = idx
                    break
                lines.insert(insert_at, 'from typing import Any')
            content = '\n'.join(lines)

    return content


def _stage_workflow(source_dir: Path, staging_root: Path, workflow_name: str) -> Path:
    """
    Copy a single workflow into *staging_root*, adapting YAML files
    to match the runtime schema and fixing Python tools for AG2 compatibility.
    """
    src = source_dir / workflow_name
    dst = staging_root / workflow_name
    dst.mkdir(parents=True, exist_ok=True)

    for item in src.iterdir():
        if item.is_dir():
            # For tools directory, we need to adapt Python files
            if item.name == "tools":
                tools_dst = dst / "tools"
                tools_dst.mkdir(exist_ok=True)
                for tool_file in item.iterdir():
                    if tool_file.suffix == ".py":
                        content = tool_file.read_text(encoding="utf-8")
                        adapted = _adapt_python_tool(content)
                        (tools_dst / tool_file.name).write_text(adapted, encoding="utf-8")
                    elif tool_file.is_dir():
                        shutil.copytree(tool_file, tools_dst / tool_file.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(tool_file, tools_dst / tool_file.name)
            else:
                shutil.copytree(item, dst / item.name, dirs_exist_ok=True)
        elif item.suffix == ".yaml":
            with open(item, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            if item.name == "orchestrator.yaml":
                adapted = _adapt_orchestrator(raw)
            else:
                adapted = _adapt_yaml_file(item.name, raw)

            with open(dst / item.name, "w", encoding="utf-8") as f:
                yaml.dump(adapted, f, default_flow_style=False, sort_keys=False)
        else:
            shutil.copy2(item, dst / item.name)

    return dst


def _create_stub_modules(staging_root: Path):
    """Create stub modules for platform-specific dependencies.

    The AgentGenerator workflow has tools that import from app.plugins
    and app.workflows which don't exist in standalone CLI mode.
    """
    import types

    # Create a stub directory structure for __path__ to point to
    stub_dir = staging_root / '_stubs'
    stub_dir.mkdir(exist_ok=True)
    (stub_dir / 'app').mkdir(exist_ok=True)
    (stub_dir / 'app' / 'plugins').mkdir(exist_ok=True)
    (stub_dir / 'app' / 'workflows').mkdir(exist_ok=True)
    (stub_dir / 'app' / 'workflows' / '_shared').mkdir(exist_ok=True)

    # Create stub 'app' package with plugins and workflows submodules
    app_module = types.ModuleType('app')
    app_module.__path__ = [str(stub_dir / 'app')]
    app_module.__file__ = str(stub_dir / 'app' / '__init__.py')

    plugins_module = types.ModuleType('app.plugins')
    plugins_module.__path__ = [str(stub_dir / 'app' / 'plugins')]
    plugins_module.__file__ = str(stub_dir / 'app' / 'plugins' / '__init__.py')
    plugins_module.get_all_plugins = lambda: []
    plugins_module.get_plugin = lambda name: None

    workflows_module = types.ModuleType('app.workflows')
    workflows_module.__path__ = [str(stub_dir / 'app' / 'workflows')]
    workflows_module.__file__ = str(stub_dir / 'app' / 'workflows' / '__init__.py')
    workflows_module.get_workflow = lambda name: None
    workflows_module.list_workflows = lambda: []

    # Stub app.workflows._shared
    workflows_shared = types.ModuleType('app.workflows._shared')
    workflows_shared.__path__ = [str(stub_dir / 'app' / 'workflows' / '_shared')]
    workflows_shared.__file__ = str(stub_dir / 'app' / 'workflows' / '_shared' / '__init__.py')

    # Stub app.plugins.workflow_tools
    workflow_tools = types.ModuleType('app.plugins.workflow_tools')
    workflow_tools.__file__ = str(stub_dir / 'app' / 'plugins' / 'workflow_tools.py')
    workflow_tools.WorkflowTools = type('WorkflowTools', (), {})

    # Stub app.plugins.ui_tools
    ui_tools = types.ModuleType('app.plugins.ui_tools')
    ui_tools.__file__ = str(stub_dir / 'app' / 'plugins' / 'ui_tools.py')

    # Stub app.plugins.persistence
    persistence = types.ModuleType('app.plugins.persistence')
    persistence.__file__ = str(stub_dir / 'app' / 'plugins' / 'persistence.py')
    # Add common persistence functions as stubs
    persistence.save_to_db = lambda *args, **kwargs: None
    persistence.load_from_db = lambda *args, **kwargs: None
    persistence.get_attachment = lambda *args, **kwargs: None
    persistence.save_attachment = lambda *args, **kwargs: None

    # Stub app.plugins.transport
    transport = types.ModuleType('app.plugins.transport')
    transport.__file__ = str(stub_dir / 'app' / 'plugins' / 'transport.py')
    transport.send_message = lambda *args, **kwargs: None
    transport.get_transport = lambda *args, **kwargs: None

    sys.modules['app'] = app_module
    sys.modules['app.plugins'] = plugins_module
    sys.modules['app.workflows'] = workflows_module
    sys.modules['app.workflows._shared'] = workflows_shared
    sys.modules['app.plugins.workflow_tools'] = workflow_tools
    sys.modules['app.plugins.ui_tools'] = ui_tools
    sys.modules['app.plugins.persistence'] = persistence
    sys.modules['app.plugins.transport'] = transport


def _setup_environment(repo_root: Path, staging_workflows: Path):
    """Configure sys.path and env vars for workflow execution."""
    for p in (str(repo_root), str(repo_root / "mozaiksai")):
        if p not in sys.path:
            sys.path.insert(0, p)

    # Point the workflow manager at our staging directory
    os.environ["MOZAIKS_WORKFLOWS_PATH"] = str(staging_workflows)

    # Create stub modules for platform-specific dependencies
    _create_stub_modules(staging_workflows)


# ── Runner ─────────────────────────────────────────────────────────

async def _run_generator(
    mode: str,
    prompt: str,
    output_dir: Path,
    workflow_name: str = "AgentGenerator",
) -> Dict[str, Any]:
    """
    Run the generator workflow with the user's prompt.

    Sets ``is_child_workflow=True`` so InterviewAgent immediately
    emits ``NEXT`` and hands off to PatternAgent.
    """
    from mozaiksai.core.workflow.orchestration_patterns import run_workflow_orchestration

    app_id = f"cli_gen_{uuid.uuid4().hex[:8]}"
    chat_id = f"gen_{uuid.uuid4().hex[:12]}"

    context_variables = {
        "concept_overview": prompt,
        "is_child_workflow": True,
        "startup_mode": "UserDriven",
        "human_in_the_loop": False,
        "monetization_enabled": False,
        "context_aware": True,
        "output_dir": str(output_dir),
        "generation_mode": mode,
    }

    def context_factory():
        return context_variables

    _print_info(f"Workflow: {workflow_name}  Mode: {mode}")
    _print_info(f"Output:   {output_dir}")

    try:
        result = await run_workflow_orchestration(
            workflow_name=workflow_name,
            app_id=app_id,
            chat_id=chat_id,
            user_id="cli_user",
            initial_message=prompt,
            context_factory=context_factory,
        )
        return {"success": True, "app_id": app_id, "chat_id": chat_id, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e), "app_id": app_id, "chat_id": chat_id}


def _create_output_structure(output_dir: Path, mode: str):
    """Create the output directory structure."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "workflows").mkdir(exist_ok=True)

    if mode == "app":
        (output_dir / "plugins").mkdir(exist_ok=True)
        (output_dir / "config").mkdir(exist_ok=True)
        (output_dir / "frontend").mkdir(exist_ok=True)


def run(args):
    """Execute the gen command."""
    mode = args.mode
    prompt = args.prompt
    output_dir = Path(args.output) if args.output else Path.cwd() / "generated"

    if not prompt:
        _print_error("Please provide a description with --prompt")
        _print_info('Example: mozaiks gen workflow --prompt "A customer support chatbot that handles refunds"')
        return 1

    if len(prompt) < 20:
        _print_error("Please provide a more detailed description (at least 20 characters)")
        _print_info("The more detail you provide, the better the generated output will be.")
        return 1

    if not _check_api_key():
        _print_error("No LLM API key found.")
        _print_info("Set one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, or AZURE_OPENAI_API_KEY")
        return 1

    source_path = _find_generator_source()
    if not source_path:
        _print_error("Could not find AgentGenerator workflow.")
        _print_info("Ensure you are in the mozaiks repo or set MOZAIKS_WORKFLOWS_PATH")
        return 1

    _print("\nMozaiks Quick Generator", style="bold cyan")
    _print(f"   Mode: {mode}", style="dim")
    _print(f"   Output: {output_dir}\n", style="dim")

    # Resolve repo root (for sys.path setup)
    # gen.py lives at mozaiks/mozaiks_cli/commands/gen.py → parents[2] = repo root
    repo_root = Path(__file__).parents[2]

    # Stage the workflow into a temp directory with schema adaptation
    staging_dir = Path(tempfile.mkdtemp(prefix="mozaiks_gen_"))
    try:
        _print_info("Staging AgentGenerator workflow...")
        _stage_workflow(source_path, staging_dir, "AgentGenerator")

        # Set up runtime environment pointing at staged workflows
        _setup_environment(repo_root, staging_dir)

        _create_output_structure(output_dir, mode)

        if console:
            console.print(Panel(prompt, title="Your Description", border_style="blue"))
        else:
            print(f"\nDescription: {prompt}\n")

        _print("\nGenerating... (this may take a few minutes)\n", style="yellow")

        if RICH_AVAILABLE:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Running AgentGenerator...", total=None)
                result = asyncio.run(_run_generator(mode=mode, prompt=prompt, output_dir=output_dir))
                progress.update(task, completed=True)
        else:
            print("Running AgentGenerator...")
            result = asyncio.run(_run_generator(mode=mode, prompt=prompt, output_dir=output_dir))

        if result.get("success"):
            _print_success("Generation complete!")
            _print_info(f"Files written to: {output_dir}")

            _print("\nGenerated files:", style="bold")
            for f in sorted(output_dir.rglob("*")):
                if f.is_file():
                    _print_info(f"  {f.relative_to(output_dir)}")

            _print("\nNext steps:", style="bold")
            _print_info(f"  cd {output_dir}")
            _print_info("  # Review and customise the generated files")
            if mode == "app":
                _print_info("  mozaiks dev  # Start development server")
            return 0
        else:
            _print_error(f"Generation failed: {result.get('error', 'Unknown error')}")
            return 1

    except KeyboardInterrupt:
        _print("\n\nGeneration cancelled.", style="yellow")
        return 130
    except Exception as e:
        _print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        # Clean up staging directory
        shutil.rmtree(staging_dir, ignore_errors=True)


def run_interactive(args):
    """Run in interactive mode - prompt user for input."""
    _print("\n🚀 Mozaiks Quick Generator (Interactive Mode)\n", style="bold cyan")

    # Ask for mode
    _print("What do you want to generate?", style="bold")
    _print("  1. workflow - AI agent workflow only")
    _print("  2. app      - Full application with workflows\n")

    mode_input = input("Choice [1/2]: ").strip()
    if mode_input == "1" or mode_input.lower() == "workflow":
        args.mode = "workflow"
    elif mode_input == "2" or mode_input.lower() == "app":
        args.mode = "app"
    else:
        _print_error("Invalid choice. Please enter 1 or 2.")
        return 1

    # Ask for description
    _print("\nDescribe what you want to build (be detailed):", style="bold")
    _print("(Press Enter twice when done)\n", style="dim")

    lines = []
    while True:
        try:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        except EOFError:
            break

    args.prompt = "\n".join(lines).strip()

    if not args.prompt:
        _print_error("No description provided.")
        return 1

    # Ask for output directory
    default_output = f"./generated-{args.mode}"
    output_input = input(f"\nOutput directory [{default_output}]: ").strip()
    args.output = output_input if output_input else default_output

    return run(args)
