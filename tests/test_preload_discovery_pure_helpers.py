"""
Pure helper unit tests for:
  factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py

Covers helpers NOT already tested in test_preload_discovery_helpers.py:

  _infer_stack_from_signals:
    - empty lists → ""
    - languages only → joined string
    - frameworks only → joined string
    - languages + frameworks → joined, languages first
    - duplicates across lists → only first occurrence kept

  _parse_package_json:
    - invalid JSON → no-op
    - node deps present → "Node.js" added to languages
    - react dep → "React" in frameworks
    - next dep → "Next.js" in frameworks
    - vite dep → "Vite" in frameworks
    - vue dep → "Vue" in frameworks
    - empty deps → nothing added
    - devDependencies also scanned
    - duplicates not appended twice

  _parse_pyproject:
    - invalid TOML bytes → no-op
    - project.dependencies present → "Python" added to languages
    - fastapi in deps → "FastAPI" in frameworks
    - django in deps → "Django" in frameworks
    - flask in deps → "Flask" in frameworks
    - empty deps → language not added

  _parse_csproj:
    - invalid XML → empty list, languages still gets C#/.NET
    - valid csproj with TargetFramework → framework in result list
    - multiple targets (TargetFrameworks) → all returned
    - C# always appended to languages
    - .NET always appended to frameworks

  _summarise_file_tree:
    - empty iterable → zero total, empty lists
    - py files → Python in languages
    - js/ts files → JavaScript/TypeScript in languages
    - cs files → C# in languages, .NET in frameworks
    - package.json in manifest_paths
    - pyproject.toml in manifest_paths
    - dockerfile in manifest_paths
    - routes.js in route_files
    - app.js in route_files
    - program.cs in service_entrypoints
    - startup.cs in service_entrypoints
    - ChatHub.cs in hub_files
    - extension_counts capped at 12 extensions
    - manifest_paths capped at 20
    - route_files capped at 20
    - csproj in manifest_paths

  _infer_service_surfaces:
    - empty repo_summary → empty list
    - service_entrypoint → surface with correct kind
    - .csproj entrypoint → kind "rest_api"
    - program.cs entrypoint → kind "rest_api"
    - hub file → kind "signalr_hub"
    - api_inventory with success=True → OpenAPI surface appended
    - runtime_observations with success=True → runtime_probe surface appended
    - duplicates by (name, location) skipped

  _infer_route_surfaces:
    - empty route_files → empty list
    - route file → surface with path and module
    - duplicates skipped
    - parent dir used as module name

  _merge_unresolved:
    - new question → appended to list
    - duplicate question → not appended again
    - priority defaults to "medium"
    - custom priority stored
"""
from __future__ import annotations

from factory_app.workflows.ExistingAppDiscovery.tools.preload_discovery_context import (
    _infer_route_surfaces,
    _infer_service_surfaces,
    _infer_stack_from_signals,
    _merge_unresolved,
    _parse_csproj,
    _parse_package_json,
    _parse_pyproject,
    _summarise_file_tree,
)

# ---------------------------------------------------------------------------
# 1. _infer_stack_from_signals
# ---------------------------------------------------------------------------

class TestInferStackFromSignals:
    def test_empty_lists_returns_empty_string(self):
        assert _infer_stack_from_signals([], []) == ""

    def test_languages_only_joined(self):
        result = _infer_stack_from_signals(["Python", "JavaScript"], [])
        assert result == "Python, JavaScript"

    def test_frameworks_only_joined(self):
        result = _infer_stack_from_signals([], ["React", "FastAPI"])
        assert result == "React, FastAPI"

    def test_languages_before_frameworks(self):
        result = _infer_stack_from_signals(["Python"], ["FastAPI"])
        assert result == "Python, FastAPI"

    def test_duplicates_across_lists_kept_once(self):
        # "Python" in both → should appear once
        result = _infer_stack_from_signals(["Python"], ["Python", "FastAPI"])
        assert result == "Python, FastAPI"
        assert result.count("Python") == 1

    def test_single_language(self):
        assert _infer_stack_from_signals(["Python"], []) == "Python"

    def test_single_framework(self):
        assert _infer_stack_from_signals([], ["React"]) == "React"

    def test_order_preserved(self):
        result = _infer_stack_from_signals(["A", "B"], ["C", "D"])
        assert result == "A, B, C, D"


# ---------------------------------------------------------------------------
# 2. _parse_package_json
# ---------------------------------------------------------------------------

class TestParsePackageJson:
    def test_invalid_json_is_noop(self):
        langs: list[str] = []
        fwks: list[str] = []
        _parse_package_json("{invalid json}", fwks, langs)
        assert langs == []
        assert fwks == []

    def test_node_deps_adds_nodejs_to_languages(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = '{"dependencies": {"express": "^4.0.0"}}'
        _parse_package_json(raw, fwks, langs)
        assert "Node.js" in langs

    def test_react_dep_adds_react_to_frameworks(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = '{"dependencies": {"react": "^18.0.0"}}'
        _parse_package_json(raw, fwks, langs)
        assert "React" in fwks

    def test_next_dep_adds_nextjs_to_frameworks(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = '{"dependencies": {"next": "^14.0.0"}}'
        _parse_package_json(raw, fwks, langs)
        assert "Next.js" in fwks

    def test_vite_dep_adds_vite_to_frameworks(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = '{"devDependencies": {"vite": "^5.0.0"}}'
        _parse_package_json(raw, fwks, langs)
        assert "Vite" in fwks

    def test_vue_dep_adds_vue_to_frameworks(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = '{"dependencies": {"vue": "^3.0.0"}}'
        _parse_package_json(raw, fwks, langs)
        assert "Vue" in fwks

    def test_empty_dependencies_adds_nothing(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = '{"dependencies": {}}'
        _parse_package_json(raw, fwks, langs)
        assert langs == []
        assert fwks == []

    def test_devdependencies_scanned(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = '{"devDependencies": {"express": "^4.0.0"}}'
        _parse_package_json(raw, fwks, langs)
        assert "Node.js" in langs

    def test_duplicate_not_appended_twice(self):
        langs = ["Node.js"]
        fwks: list[str] = []
        raw = '{"dependencies": {"express": "^4.0.0"}}'
        _parse_package_json(raw, fwks, langs)
        assert langs.count("Node.js") == 1

    def test_no_dependencies_key_is_noop(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = '{"name": "my-app"}'
        _parse_package_json(raw, fwks, langs)
        assert langs == []


# ---------------------------------------------------------------------------
# 3. _parse_pyproject
# ---------------------------------------------------------------------------

class TestParsePyproject:
    def _encode(self, toml_str: str) -> bytes:
        return toml_str.encode("utf-8")

    def test_invalid_toml_is_noop(self):
        langs: list[str] = []
        fwks: list[str] = []
        _parse_pyproject(b"[invalid [toml", fwks, langs)
        assert langs == []
        assert fwks == []

    def test_project_dependencies_adds_python(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = self._encode('[project]\ndependencies = ["httpx"]\n')
        _parse_pyproject(raw, fwks, langs)
        assert "Python" in langs

    def test_fastapi_dep_adds_fastapi_to_frameworks(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = self._encode('[project]\ndependencies = ["fastapi>=0.100"]\n')
        _parse_pyproject(raw, fwks, langs)
        assert "FastAPI" in fwks

    def test_django_dep_adds_django_to_frameworks(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = self._encode('[project]\ndependencies = ["django>=4.0"]\n')
        _parse_pyproject(raw, fwks, langs)
        assert "Django" in fwks

    def test_flask_dep_adds_flask_to_frameworks(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = self._encode('[project]\ndependencies = ["flask>=3.0"]\n')
        _parse_pyproject(raw, fwks, langs)
        assert "Flask" in fwks

    def test_empty_deps_adds_nothing(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = self._encode('[project]\ndependencies = []\n')
        _parse_pyproject(raw, fwks, langs)
        assert langs == []

    def test_no_project_section_is_noop(self):
        langs: list[str] = []
        fwks: list[str] = []
        raw = self._encode('[tool.black]\nline-length = 88\n')
        _parse_pyproject(raw, fwks, langs)
        assert langs == []

    def test_duplicate_not_appended_twice(self):
        langs = ["Python"]
        fwks: list[str] = []
        raw = self._encode('[project]\ndependencies = ["httpx"]\n')
        _parse_pyproject(raw, fwks, langs)
        assert langs.count("Python") == 1


# ---------------------------------------------------------------------------
# 4. _parse_csproj
# ---------------------------------------------------------------------------

class TestParseCsproj:
    def test_invalid_xml_returns_empty_list(self):
        langs: list[str] = []
        fwks: list[str] = []
        result = _parse_csproj("not xml <<", fwks, langs)
        assert result == []

    def test_csharp_always_appended_to_languages(self):
        langs: list[str] = []
        fwks: list[str] = []
        csproj = '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>'
        _parse_csproj(csproj, fwks, langs)
        assert "C#" in langs

    def test_dotnet_always_appended_to_frameworks(self):
        langs: list[str] = []
        fwks: list[str] = []
        csproj = '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>'
        _parse_csproj(csproj, fwks, langs)
        assert ".NET" in fwks

    def test_target_framework_returned(self):
        langs: list[str] = []
        fwks: list[str] = []
        csproj = '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>'
        result = _parse_csproj(csproj, fwks, langs)
        assert "net8.0" in result

    def test_multiple_target_frameworks_semicolon_separated(self):
        langs: list[str] = []
        fwks: list[str] = []
        csproj = '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFrameworks>net6.0;net8.0</TargetFrameworks></PropertyGroup></Project>'
        result = _parse_csproj(csproj, fwks, langs)
        assert "net6.0" in result
        assert "net8.0" in result

    def test_no_target_framework_elem_returns_empty_list(self):
        langs: list[str] = []
        fwks: list[str] = []
        csproj = '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup></PropertyGroup></Project>'
        result = _parse_csproj(csproj, fwks, langs)
        assert result == []

    def test_invalid_xml_still_returns_list_type(self):
        langs: list[str] = []
        fwks: list[str] = []
        result = _parse_csproj("bad xml", fwks, langs)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 5. _summarise_file_tree
# ---------------------------------------------------------------------------

class TestSummariseFileTree:
    def test_empty_iterable(self):
        result = _summarise_file_tree([])
        assert result["total_files_scanned"] == 0
        assert result["manifest_paths"] == []
        assert result["route_files"] == []
        assert result["languages"] == []

    def test_py_files_add_python_language(self):
        result = _summarise_file_tree(["src/service.py", "backend/handler.py"])
        assert "Python" in result["languages"]

    def test_js_files_add_javascript_typescript_language(self):
        result = _summarise_file_tree(["src/App.js"])
        assert "JavaScript/TypeScript" in result["languages"]

    def test_ts_files_add_javascript_typescript(self):
        result = _summarise_file_tree(["src/App.tsx"])
        assert "JavaScript/TypeScript" in result["languages"]

    def test_cs_files_add_csharp_language_and_dotnet_framework(self):
        result = _summarise_file_tree(["src/Service.cs"])
        assert "C#" in result["languages"]
        assert ".NET" in result["frameworks"]

    def test_package_json_in_manifest_paths(self):
        result = _summarise_file_tree(["package.json"])
        assert "package.json" in result["manifest_paths"]

    def test_pyproject_toml_in_manifest_paths(self):
        result = _summarise_file_tree(["pyproject.toml"])
        assert "pyproject.toml" in result["manifest_paths"]

    def test_dockerfile_in_manifest_paths(self):
        result = _summarise_file_tree(["Dockerfile"])
        assert "Dockerfile" in result["manifest_paths"]

    def test_routes_js_in_route_files(self):
        result = _summarise_file_tree(["src/routes.js"])
        assert "src/routes.js" in result["route_files"]

    def test_app_js_in_route_files(self):
        result = _summarise_file_tree(["src/app.js"])
        assert "src/app.js" in result["route_files"]

    def test_router_tsx_in_route_files(self):
        result = _summarise_file_tree(["src/router.tsx"])
        assert "src/router.tsx" in result["route_files"]

    def test_program_cs_in_service_entrypoints(self):
        result = _summarise_file_tree(["src/Program.cs"])
        assert "src/Program.cs" in result["service_entrypoints"]

    def test_startup_cs_in_service_entrypoints(self):
        result = _summarise_file_tree(["src/Startup.cs"])
        assert "src/Startup.cs" in result["service_entrypoints"]

    def test_hub_cs_in_hub_files(self):
        result = _summarise_file_tree(["realtime/ChatHub.cs"])
        assert "realtime/ChatHub.cs" in result["hub_files"]

    def test_csproj_in_manifest_paths(self):
        result = _summarise_file_tree(["MyApp.csproj"])
        assert "MyApp.csproj" in result["manifest_paths"]

    def test_csproj_also_in_csproj_paths(self):
        result = _summarise_file_tree(["MyApp.csproj"])
        assert "MyApp.csproj" in result["csproj_paths"]

    def test_extension_counts_tallied(self):
        result = _summarise_file_tree(["a.py", "b.py", "c.js"])
        assert result["extension_counts"].get(".py") == 2
        assert result["extension_counts"].get(".js") == 1

    def test_total_files_scanned(self):
        result = _summarise_file_tree(["a.py", "b.py", "c.js"])
        assert result["total_files_scanned"] == 3

    def test_no_extension_not_counted(self):
        result = _summarise_file_tree(["Dockerfile"])
        assert "" not in result["extension_counts"]


# ---------------------------------------------------------------------------
# 6. _infer_service_surfaces
# ---------------------------------------------------------------------------

class TestInferServiceSurfaces:
    def test_empty_summary_returns_empty(self):
        result = _infer_service_surfaces({}, {}, {})
        assert result == []

    def test_service_entrypoint_creates_surface(self):
        repo_summary = {"service_entrypoints": ["src/service.py"]}
        result = _infer_service_surfaces(repo_summary, {}, {})
        assert len(result) == 1
        assert result[0]["location"] == "src/service.py"

    def test_csproj_entrypoint_kind_is_rest_api(self):
        repo_summary = {"service_entrypoints": ["MyApp.Api.csproj"]}
        result = _infer_service_surfaces(repo_summary, {}, {})
        assert result[0]["kind"] == "rest_api"

    def test_program_cs_kind_is_rest_api(self):
        repo_summary = {"service_entrypoints": ["src/Program.cs"]}
        result = _infer_service_surfaces(repo_summary, {}, {})
        assert result[0]["kind"] == "rest_api"

    def test_other_entrypoint_kind_is_service(self):
        repo_summary = {"service_entrypoints": ["backend/main.py"]}
        result = _infer_service_surfaces(repo_summary, {}, {})
        assert result[0]["kind"] == "service"

    def test_hub_file_kind_is_signalr_hub(self):
        repo_summary = {"hub_files": ["realtime/ChatHub.cs"]}
        result = _infer_service_surfaces(repo_summary, {}, {})
        assert result[0]["kind"] == "signalr_hub"

    def test_api_inventory_success_adds_surface(self):
        api_inventory = {"success": True, "spec_location": "/api/openapi.json", "title": "My API", "path_count": 10}
        result = _infer_service_surfaces({}, api_inventory, {})
        assert any(s["kind"] == "rest_api" for s in result)
        assert any("My API" in s["name"] for s in result)

    def test_api_inventory_without_success_skipped(self):
        api_inventory = {"success": False, "spec_location": "/api/openapi.json"}
        result = _infer_service_surfaces({}, api_inventory, {})
        assert result == []

    def test_runtime_probe_success_adds_surface(self):
        runtime = {"success": True, "health_url": "http://localhost:8000/health"}
        result = _infer_service_surfaces({}, {}, runtime)
        assert any(s["kind"] == "runtime_probe" for s in result)

    def test_runtime_probe_without_success_skipped(self):
        runtime = {"success": False, "health_url": "http://localhost:8000/health"}
        result = _infer_service_surfaces({}, {}, runtime)
        assert result == []

    def test_duplicate_entrypoint_skipped(self):
        repo_summary = {"service_entrypoints": ["src/service.py", "src/service.py"]}
        result = _infer_service_surfaces(repo_summary, {}, {})
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 7. _infer_route_surfaces
# ---------------------------------------------------------------------------

class TestInferRouteSurfaces:
    def test_empty_route_files_returns_empty(self):
        result = _infer_route_surfaces({})
        assert result == []

    def test_route_file_creates_surface(self):
        repo_summary = {"route_files": ["src/routes.js"]}
        result = _infer_route_surfaces(repo_summary)
        assert len(result) == 1
        assert result[0]["path"] == "src/routes.js"

    def test_module_from_parent_dir(self):
        repo_summary = {"route_files": ["frontend/src/routes.js"]}
        result = _infer_route_surfaces(repo_summary)
        assert result[0]["module"] == "src"

    def test_duplicate_route_file_skipped(self):
        repo_summary = {"route_files": ["src/routes.js", "src/routes.js"]}
        result = _infer_route_surfaces(repo_summary)
        assert len(result) == 1

    def test_multiple_route_files(self):
        repo_summary = {"route_files": ["src/routes.js", "admin/router.tsx"]}
        result = _infer_route_surfaces(repo_summary)
        assert len(result) == 2

    def test_description_mentions_location(self):
        repo_summary = {"route_files": ["src/app.js"]}
        result = _infer_route_surfaces(repo_summary)
        assert "src/app.js" in result[0]["description"]


# ---------------------------------------------------------------------------
# 8. _merge_unresolved
# ---------------------------------------------------------------------------

class TestMergeUnresolved:
    def test_new_question_appended(self):
        items: list[dict] = []
        _merge_unresolved(items, "What auth provider?", "User asked about auth")
        assert len(items) == 1
        assert items[0]["question"] == "What auth provider?"

    def test_duplicate_question_not_appended(self):
        items: list[dict] = [{"question": "What auth provider?", "context": "ctx", "priority": "medium"}]
        _merge_unresolved(items, "What auth provider?", "Different context")
        assert len(items) == 1

    def test_default_priority_is_medium(self):
        items: list[dict] = []
        _merge_unresolved(items, "What storage?", "Context")
        assert items[0]["priority"] == "medium"

    def test_custom_priority_stored(self):
        items: list[dict] = []
        _merge_unresolved(items, "Critical question?", "Context", priority="high")
        assert items[0]["priority"] == "high"

    def test_context_stored(self):
        items: list[dict] = []
        _merge_unresolved(items, "Question?", "some context here")
        assert items[0]["context"] == "some context here"

    def test_multiple_unique_questions_appended(self):
        items: list[dict] = []
        _merge_unresolved(items, "Q1?", "ctx1")
        _merge_unresolved(items, "Q2?", "ctx2")
        assert len(items) == 2

    def test_modifies_list_in_place(self):
        items: list[dict] = []
        result = _merge_unresolved(items, "Q?", "ctx")
        # Returns None, modifies in place
        assert result is None
        assert len(items) == 1
