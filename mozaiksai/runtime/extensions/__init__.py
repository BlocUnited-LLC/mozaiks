"""Runtime extensions — declared routers, services, platform hooks."""

from mozaiksai.runtime.extensions.extensions import (
    mount_declared_routers,
    start_declared_services,
    stop_services,
)
from mozaiksai.runtime.extensions.platform_hooks import get_platform_hooks

