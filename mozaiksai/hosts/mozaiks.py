from __future__ import annotations

"""Mozaiks hosted product host layered on top of mozaiksai.hosts.studio.

This is the hosted Mozaiks product composition. It currently reuses the local
Studio management and create surface and is the place for product-only
additions such as collaboration, marketplace, billing, and hosted workspace
behavior.
"""

from mozaiksai.hosts.bootstrap import configure_repo_host_defaults

configure_repo_host_defaults("mozaiks")

from mozaiksai.hosts import studio as studio_app
from logs.logging_config import get_workflow_logger


app = studio_app.app
logger = get_workflow_logger("mozaiks_app")


__all__ = ["app"]
