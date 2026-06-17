"""Steam account-switch detection and data migration.

Ported from ``staging`` (pre-refactor monolith) during the mixin
split — the account-switch modal RPCs (``check_account_switch`` /
``migrate_account_data``) were never carried over, so the modal
never appeared. See :class:`AccountManager`.
"""

from .account_manager import AccountManager

__all__ = ["AccountManager"]
