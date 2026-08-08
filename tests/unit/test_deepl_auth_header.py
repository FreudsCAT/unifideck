"""Regression: the DeepL key travels in the Authorization header.

Field failure — the ``build`` workflow's i18n step died on every run that
actually had a key, with DeepL returning::

    403 {"message": "Missing Authorization header, expected
         'Authorization: DeepL-Auth-Key <API key>'. ..."}

``_post`` was sending the key as an ``auth_key`` form field, which DeepL no
longer accepts; the header is the only form they document. Because the step
runs first in ``build-plugin.yml``, the workflow never reached packaging, so
no plugin zip was produced for any PR.

The key must ALSO stay out of the request body: leaving it there would keep
working by accident the day DeepL re-enabled the old path, and meanwhile it
is a credential written into a place (form data) that proxies and error
dumps log more readily than headers.

``scripts/`` is not on ``pythonpath`` (pyproject lists only ``py_modules``),
so the import is bootstrapped the same way ``utils/locale.py`` does it.
"""
from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import deepl_client as dc

_FREE_KEY = "279a2e9d-83b3-c416-7e2d-f721593e42a0:fx"
_PRO_KEY = "279a2e9d-83b3-c416-7e2d-f721593e42a0"


@pytest.fixture
def captured(monkeypatch):
    """Capture the urllib Request without touching the network."""
    seen: dict[str, object] = {}

    class _Resp:
        def __init__(self, count: int):
            self._count = count

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            items = ",".join('{"text":"Bonjour"}' for _ in range(self._count))
            return f'{{"translations":[{items}]}}'.encode()

    def _fake_urlopen(req, timeout=None):
        seen["headers"] = dict(req.headers)
        seen["url"] = req.full_url
        seen["body"] = req.data.decode("utf-8")
        # Echo back one translation per input so the caller's arity check
        # (which is not what these tests are about) is satisfied.
        n = len(urllib.parse.parse_qs(seen["body"]).get("text", []))
        return _Resp(n)

    monkeypatch.setattr(dc.urllib.request, "urlopen", _fake_urlopen)
    return seen


def test_key_is_sent_as_authorization_header(captured):
    dc.translate_batch(["Hello"], "FR", "EN", _FREE_KEY)

    # urllib title-cases header names it stores.
    assert captured["headers"]["Authorization"] == (
        f"DeepL-Auth-Key {_FREE_KEY}"
    )


def test_key_is_not_in_the_request_body(captured):
    dc.translate_batch(["Hello"], "FR", "EN", _FREE_KEY)

    body = urllib.parse.parse_qs(captured["body"])
    assert "auth_key" not in body
    assert _FREE_KEY not in captured["body"]


def test_translation_params_survive(captured):
    """The header change must not disturb what we actually ask DeepL for."""
    dc.translate_batch(["Hello", "Bye"], "FR", "EN", _FREE_KEY)

    body = urllib.parse.parse_qs(captured["body"])
    assert body["target_lang"] == ["FR"]
    assert body["source_lang"] == ["EN"]
    assert body["preserve_formatting"] == ["1"]
    assert body["tag_handling"] == ["xml"]
    assert body["text"] == ["Hello", "Bye"]


@pytest.mark.parametrize(
    ("key", "expected"),
    [(_FREE_KEY, dc.FREE_URL), (_PRO_KEY, dc.PRO_URL)],
)
def test_endpoint_still_follows_the_key_suffix(captured, key, expected):
    dc.translate_batch(["Hello"], "FR", "EN", key)

    assert captured["url"] == expected
