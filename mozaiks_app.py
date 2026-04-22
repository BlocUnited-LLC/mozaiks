from __future__ import annotations

"""Mozaiks App Zero host layered on top of studio_app.py.

This is the hosted Mozaiks product composition. It currently reuses the local
Studio builder surface and is the place for product-only additions such as
collaboration, marketplace, billing, and hosted workspace behavior.
"""

import studio_app
from logs.logging_config import get_workflow_logger


app = studio_app.app
logger = get_workflow_logger("mozaiks_app")


__all__ = ["app"]
