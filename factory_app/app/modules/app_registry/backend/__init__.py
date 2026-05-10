from importlib import import_module

__all__ = [
    "APP_LIFECYCLE_STATES",
    "AppRegistryModule",
    "AppRegistryRepo",
    "AppRegistryService",
]


def __getattr__(name: str):
    if name == "APP_LIFECYCLE_STATES":
        return getattr(import_module(".policy", __name__), name)
    if name in {"AppRegistryModule", "AppRegistryRepo", "AppRegistryService"}:
        module_name = {
            "AppRegistryModule": ".handler",
            "AppRegistryRepo": ".repo",
            "AppRegistryService": ".service",
        }[name]
        return getattr(import_module(module_name, __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
