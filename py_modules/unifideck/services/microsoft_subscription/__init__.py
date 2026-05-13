"""Microsoft subscription service — Game Pass / xCloud subscription state.

OP-22 | py_modules/unifideck/services/microsoft_subscription/__init__.py

Re-exports ``MicrosoftSubscriptionService``. The service polls
Microsoft endpoints to determine the user's active subscription
(Game Pass Ultimate / Core / Essential / xCloud) and exposes that
state to the rest of the plugin (used by the Microsoft store to
filter the visible games to those the subscription includes).
"""

from __future__ import annotations
from .service import MicrosoftSubscriptionService

__all__ = ["MicrosoftSubscriptionService"]
