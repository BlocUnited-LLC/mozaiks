"""App-owned extension of the deterministic content actions."""
from .base_handler import ContentBaseHandler


class ContentHandler(ContentBaseHandler):
    """Extend the declared content actions through this workspace-owned class."""
