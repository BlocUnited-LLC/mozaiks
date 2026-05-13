"""Re-export platform build lifecycle hooks shared across factory workflows."""
from factory_app.workflows.AppGenerator.tools.platform.build_lifecycle import (  # noqa: F401
    emit_build_started,
    emit_build_completed,
    emit_build_failed,
)
