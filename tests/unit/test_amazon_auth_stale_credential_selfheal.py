"""Regression: Amazon sign-in must recover from nile's stale credentials.

Bug report (r/Unifideck, 0.7.0 feedback): Amazon library sync kept failing
with ``get_url_failed``, and the only escape the reporter found was deleting
ALL plugin cache and signing back into every store one by one.

The wedge: ``_is_already_authed`` reports "logged in" only when nile prints
one of ``_NILE_ALREADY_AUTHED_MARKERS``. With credentials present but stale,
nile exits non-zero WITHOUT those markers, so the fast path declines and the
real flow runs — and ``_fetch_login_url`` issues the very same
``nile auth --login --non-interactive`` command, which fails identically.
Nothing ever cleared nile's state, so every retry hit the same wall.

Fix: a refusal (``_NileProbeRefusedError``, i.e. non-zero exit) clears nile's
stored credentials once and retries the probe. Other probe failures — CLI
missing, timeout, malformed JSON — must NOT trigger it: discarding a working
token because of a transient timeout would recreate the bug.
"""
from __future__ import annotations

import types

import pytest

from unifideck.core.types import StoreAuthError
from unifideck.stores.amazon.amazon_auth import AmazonAuthFlow, _NileProbeRefusedError

_LOGIN_PAYLOAD = {
    "url": "https://amazon.com/ap/signin?openid.ns=x",
    "code_verifier": "cv",
    "serial": "sn",
    "client_id": "cid",
}


def _flow() -> AmazonAuthFlow:
    return AmazonAuthFlow(
        bus=types.SimpleNamespace(emit=None),
        orchestrator=types.SimpleNamespace(),
        cli_path="/plugin/bin/nile",
        success_markers=[],
    )


def _install_probe(flow: AmazonAuthFlow, outcomes: list[object]) -> list[str]:
    """Drive ``_run_nile_login_probe`` from a scripted list of outcomes.

    Each entry is either an exception to raise or a payload to return.
    Records the call order alongside logout calls so the test can assert the
    *sequence*, not just the final result.
    """
    calls: list[str] = []
    pending = list(outcomes)

    async def fake_probe() -> dict:
        calls.append("probe")
        outcome = pending.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def fake_clear() -> bool:
        calls.append("logout")
        return True

    flow._run_nile_login_probe = fake_probe  # type: ignore[method-assign]
    # Stub the whole credential-clearing step: the real one inspects (and can
    # rename) nile's config under $HOME, which a unit test must never touch.
    flow._clear_nile_credentials = fake_clear  # type: ignore[method-assign]
    return calls


@pytest.mark.asyncio
async def test_refusal_clears_credentials_and_retry_succeeds() -> None:
    flow = _flow()
    refused = _NileProbeRefusedError("nile auth failed (rc=1): stale token", store="amazon")
    calls = _install_probe(flow, [refused, _LOGIN_PAYLOAD])

    url = await flow._fetch_login_url()

    assert url == _LOGIN_PAYLOAD["url"]
    # The logout must land BETWEEN the two probes — retrying without clearing
    # nile's state is exactly the loop this fixes.
    assert calls == ["probe", "logout", "probe"]


@pytest.mark.asyncio
async def test_second_refusal_propagates_with_nile_stderr() -> None:
    flow = _flow()
    first = _NileProbeRefusedError("nile auth failed (rc=1): first", store="amazon")
    second = _NileProbeRefusedError(
        "nile auth failed (rc=1): device deregistered", store="amazon",
    )
    calls = _install_probe(flow, [first, second])

    with pytest.raises(StoreAuthError) as excinfo:
        await flow._fetch_login_url()

    # The SECOND error wins, so the surfaced text carries nile's real reason
    # rather than a bare error code.
    assert "device deregistered" in str(excinfo.value)
    assert calls == ["probe", "logout", "probe"]


@pytest.mark.asyncio
async def test_non_refusal_failures_never_clear_credentials() -> None:
    """A timeout is not a credential problem — keep the token, fail once."""
    flow = _flow()
    timeout = StoreAuthError("nile auth timed out after 30s", store="amazon")
    calls = _install_probe(flow, [timeout])

    with pytest.raises(StoreAuthError):
        await flow._fetch_login_url()

    assert calls == ["probe"], "must not log out or retry on a non-refusal"


@pytest.mark.asyncio
async def test_no_retry_when_logout_unavailable() -> None:
    """Without a usable CLI there is nothing to clear, so don't loop."""
    flow = _flow()
    refused = _NileProbeRefusedError("nile auth failed (rc=1)", store="amazon")
    calls = _install_probe(flow, [refused])

    async def failed_clear() -> bool:
        calls.append("logout")
        return False

    flow._clear_nile_credentials = failed_clear  # type: ignore[method-assign]

    with pytest.raises(StoreAuthError):
        await flow._fetch_login_url()

    assert calls == ["probe", "logout"]


@pytest.mark.asyncio
async def test_successful_probe_leaves_credentials_alone() -> None:
    flow = _flow()
    calls = _install_probe(flow, [_LOGIN_PAYLOAD])

    await flow._fetch_login_url()

    assert calls == ["probe"]
    # The payload is retained for the code exchange that follows.
    assert flow._pending_login == _LOGIN_PAYLOAD


@pytest.mark.asyncio
async def test_probe_marks_nonzero_exit_as_refusal() -> None:
    """The real probe must raise the recoverable subtype on a non-zero exit.

    Guards the wiring the tests above stub out: if `_run_nile_login_probe`
    ever downgrades this to a plain StoreAuthError the self-heal silently
    stops running.
    """
    flow = _flow()

    class _Proc:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"nile: not registered"

    async def fake_exec(*_a: object, **_kw: object) -> _Proc:
        return _Proc()

    import asyncio as _asyncio

    original = _asyncio.create_subprocess_exec
    _asyncio.create_subprocess_exec = fake_exec  # type: ignore[assignment]
    try:
        with pytest.raises(_NileProbeRefusedError) as excinfo:
            await flow._run_nile_login_probe()
    finally:
        _asyncio.create_subprocess_exec = original  # type: ignore[assignment]

    assert "not registered" in str(excinfo.value)


@pytest.mark.asyncio
async def test_probe_bad_json_is_not_a_refusal() -> None:
    """Malformed JSON on a zero exit is not a credential problem."""
    flow = _flow()

    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"not json", b""

    async def fake_exec(*_a: object, **_kw: object) -> _Proc:
        return _Proc()

    import asyncio as _asyncio

    original = _asyncio.create_subprocess_exec
    _asyncio.create_subprocess_exec = fake_exec  # type: ignore[assignment]
    try:
        with pytest.raises(StoreAuthError) as excinfo:
            await flow._run_nile_login_probe()
    finally:
        _asyncio.create_subprocess_exec = original  # type: ignore[assignment]

    assert not isinstance(excinfo.value, _NileProbeRefusedError)
    assert "invalid JSON" in str(excinfo.value)
