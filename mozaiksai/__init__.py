# ==============================================================================
# MozaiksAI Namespace Package
# ==============================================================================
"""
MozaiksAI runtime namespace package.

This namespace allows the MozaiksAI runtime to be distributed across multiple
repositories while maintaining a unified import structure:

    from mozaiksai.core.workflow import workflow_manager
    from mozaiksai.core.transport import SimpleTransport
    from mozaiksai.core.auth import require_user_scope

The namespace is PEP 420 compliant (implicit namespace packages).
"""

__version__ = "1.0.0"
