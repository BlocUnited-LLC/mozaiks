from tests.import_utils import import_module_directly


def test_runtime_root_imports_actual_app_symbols() -> None:
    runtime_pkg = import_module_directly("mozaiksai.core.runtime")
    app_pkg = import_module_directly("mozaiksai.core.runtime.app")

    assert runtime_pkg.AppDefinition is app_pkg.AppDefinition
    assert runtime_pkg.ModuleLoader is app_pkg.ModuleLoader
    assert runtime_pkg.LoadedModule is app_pkg.LoadedModule
    assert runtime_pkg.ModuleDefinition is app_pkg.ModuleDefinition
    assert runtime_pkg.ActionDef is app_pkg.ActionDef
