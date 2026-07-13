"""Account data management — deletion and portability.

Provides the ``AccountDataHandler`` Protocol and the process-global
``account_data_registry`` that app modules use to participate in
GDPR-compliant account deletion and data portability export.

Quick start::

    from mozaiksai.core.account import AccountDataHandler, account_data_registry

See ``protocol.py`` for the full Protocol definition and usage examples.
"""
from .protocol import AccountDataHandler
from .registry import AccountDataRegistry, account_data_registry

__all__ = [
    "AccountDataHandler",
    "AccountDataRegistry",
    "account_data_registry",
]
