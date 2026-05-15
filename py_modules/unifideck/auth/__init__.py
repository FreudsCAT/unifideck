"""unifideck.auth — OAuth interception and credential helpers.

Refactored auth/browser.py exposes `OAuthBrowserMonitor`. The
legacy name was `CDPOAuthMonitor` and is preserved as an alias
in browser.py itself, so importing either form works.
"""
from .browser import CDPOAuthMonitor, OAuthBrowserMonitor

__all__ = ["CDPOAuthMonitor", "OAuthBrowserMonitor"]
