"""Engine validation sub-package."""


def __getattr__(name: str):
    if name == "SENTINEL_STATUS":
        from mozaiksai.engine.validation.tools import SENTINEL_STATUS
        return SENTINEL_STATUS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
