"""Tests for the OAuth-capture offline fast-fail.

When the Steam Deck is offline the embedded browser lands on a
navigation-error page (e.g. ``http://error/``) instead of the login
page. Previously the capture loop skipped that target and polled
uselessly until the 300s deadline. The fast-fail returns a
``network_unreachable`` result once error pages persist past a grace
window with no OAuth-relevant target in sight — while a transient
blip (or partial recovery) must NOT abort the flow.
"""

from __future__ import annotations

import pytest

from unifideck.auth import browser as browser_mod
from unifideck.auth.browser import (
    _OFFLINE_GRACE_SECONDS,
    _check_offline_fastfail,
    _init_polling_state,
)
from unifideck.auth.browser_url_parsing import (
    is_navigation_error_url,
    is_oauth_relevant_url,
)


def _targets(*urls: str) -> list[dict[str, str]]:
    """Build a CDP target list from URLs."""
    return [{"url": u} for u in urls]


def test_is_navigation_error_url_positive() -> None:
    """Known CEF error markers are detected."""
    assert is_navigation_error_url("http://error/")
    assert is_navigation_error_url("chrome-error://chromewebdata/")
    assert is_navigation_error_url("HTTP://ERROR/")


def test_is_navigation_error_url_negative() -> None:
    """Real auth / normal pages are not flagged as errors."""
    assert not is_navigation_error_url("https://auth.gog.com/auth?x=1")
    assert not is_navigation_error_url(
        "https://embed.gog.com/on_login_success?code=abc",
    )
    assert not is_navigation_error_url("https://steamloopback.host/index.html")


def test_error_pages_do_not_overlap_oauth_keywords() -> None:
    """A navigation-error URL must not also read as OAuth-relevant."""
    assert not is_oauth_relevant_url("http://error/")


def _fixed_clock(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    monkeypatch.setattr(browser_mod.time, "monotonic", lambda: value)


def test_within_grace_keeps_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """First sight of an error page starts the timer, returns None."""
    state = _init_polling_state()
    _fixed_clock(monkeypatch, 100.0)
    result = _check_offline_fastfail(_targets("http://error/"), state, 0.0)
    assert result is None
    assert state["error_since"] == 100.0


def test_past_grace_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persisting error pages past the grace window → network_unreachable."""
    state = _init_polling_state()
    _fixed_clock(monkeypatch, 100.0)
    assert _check_offline_fastfail(_targets("http://error/"), state, 0.0) is None
    _fixed_clock(monkeypatch, 100.0 + _OFFLINE_GRACE_SECONDS)
    result = _check_offline_fastfail(_targets("http://error/"), state, 0.0)
    assert result is not None
    assert result.success is False
    assert result.error == "network_unreachable"


def test_oauth_target_resets_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """An OAuth-relevant target means the network works → reset + poll."""
    state = _init_polling_state()
    _fixed_clock(monkeypatch, 100.0)
    _check_offline_fastfail(_targets("http://error/"), state, 0.0)
    assert state["error_since"] == 100.0
    _fixed_clock(monkeypatch, 105.0)
    result = _check_offline_fastfail(
        _targets("http://error/", "https://auth.gog.com/auth"), state, 0.0,
    )
    assert result is None
    assert state["error_since"] is None


def test_no_error_pages_resets_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Healthy (non-error, non-OAuth) targets clear a pending timer."""
    state = _init_polling_state()
    _fixed_clock(monkeypatch, 100.0)
    _check_offline_fastfail(_targets("http://error/"), state, 0.0)
    _fixed_clock(monkeypatch, 101.0)
    result = _check_offline_fastfail(
        _targets("https://steamloopback.host/index.html"), state, 0.0,
    )
    assert result is None
    assert state["error_since"] is None
