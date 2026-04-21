from tests.import_utils import import_module_directly


def test_runtime_root_imports_actual_app_symbols() -> None:
    runtime_pkg = import_module_directly("mozaiksai.core.runtime")
    app_pkg = import_module_directly("mozaiksai.core.runtime.app")

    assert runtime_pkg.AppDefinition is app_pkg.AppDefinition
    assert runtime_pkg.CapabilityLoader is app_pkg.CapabilityLoader
    assert runtime_pkg.LoadedCapability is app_pkg.LoadedCapability