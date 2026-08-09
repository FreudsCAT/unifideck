"""A Ubisoft credential UPC has rejected must be detectable.

We inject the auth prefix's credential into every game prefix before running
UPC. If UPC then leaves that prefix SIGNED OUT, it never accepted what we gave
it — the stored token is dead server-side (Ubisoft rotates and invalidates).

Nothing used to notice. ``capture`` correctly refuses to overwrite a
"logged-in" credential with a smaller (logged-out) one, so the dead token stays
put and every later install injects it again: an endless sign-in prompt with no
UI affordance to break out of. Confirmed live 2026-08-01 — the Aug-1 04:55 auth
credential was injected into a fresh prefix (``inject: synced 4 credential
file(s)``) and UPC demanded a sign-in anyway.

The detector is deliberately **report-only**: purging a user's credentials is
their call (QAM → Ubisoft → Sign out), never a heuristic's side effect.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from unifideck.stores.ubisoft.session.facade import UbisoftSession

# Real on-device sizes: the shapes UPC writes.
LOGGED_IN = 7612
LOGGED_OUT = 6471
PRISTINE = 1188


def _session(sizes: dict[str, int], auth_dir: str = "/auth"):
    """A session whose reader reports ``sizes`` per prefix path."""
    sess = UbisoftSession.__new__(UbisoftSession)
    sess._config = SimpleNamespace(auth_prefix_dir_expanded=auth_dir)
    sess._reader = SimpleNamespace(
        get_credential_size=lambda p: sizes.get(p, 0),
        has_valid_credentials=lambda p: sizes.get(p, 0) > PRISTINE,
    )
    return sess


def test_signed_out_prefix_after_injection_is_reported():
    """The live failure: UPC signed out of a prefix we had signed in."""
    sess = _session({"/auth": LOGGED_IN, "/games/80": LOGGED_OUT})

    assert sess.stored_credential_was_rejected("/games/80") is True


def test_healthy_prefix_is_not_reported():
    """UPC kept the session → nothing to report."""
    sess = _session({"/auth": LOGGED_IN, "/games/80": LOGGED_IN})

    assert sess.stored_credential_was_rejected("/games/80") is False


def test_no_stored_credential_is_not_a_rejection():
    """Signed out by choice → a sign-in prompt is expected, not a defect."""
    sess = _session({"/auth": 0, "/games/80": LOGGED_OUT})

    assert sess.stored_credential_was_rejected("/games/80") is False


def test_prefix_without_a_credential_is_not_a_rejection():
    """A never-run prefix has no credential to judge."""
    sess = _session({"/auth": LOGGED_IN, "/games/80": 0})

    assert sess.stored_credential_was_rejected("/games/80") is False


@pytest.mark.parametrize("bigger", [LOGGED_IN + 1, LOGGED_IN + 4096])
def test_a_larger_credential_is_never_a_rejection(bigger):
    """A refreshed token grew — that's a healthy rotation, not a rejection."""
    sess = _session({"/auth": LOGGED_IN, "/games/80": bigger})

    assert sess.stored_credential_was_rejected("/games/80") is False


def test_detector_does_not_mutate_anything():
    """Report-only: no purge, no write, no clear."""
    calls: list[str] = []
    sess = _session({"/auth": LOGGED_IN, "/games/80": LOGGED_OUT})
    for forbidden in (
        "purge_credentials_from_all", "clear_session_file", "_write_stored_mtime",
    ):
        setattr(sess, forbidden, lambda *a, **k: calls.append(forbidden))

    sess.stored_credential_was_rejected("/games/80")

    assert calls == []
