"""The signed-in / signed-out probe for the Battle.net client.

tests/unit/test_battlenet_login_state.py

Every marker asserted here was measured on a real device by diffing one
successful client session against two failed ones. The excerpts below are
copied from those logs rather than invented, because the whole probe is a
claim about a vendor's log format and a paraphrase would not test it.

The failure it exists to stop: ``client_ready`` proves a CEF renderer
exists, which is equally true of the login page, so phase C sent
``--exec="launch D1"`` into a signed-out client. The command was accepted,
nothing started, and the launch failed 180s later blaming a family-code
rename.
"""
from __future__ import annotations

import pytest

from unifideck.launcher.proton.handlers.battlenet_login_state import (
    LoginState,
    read_login_state,
    wait_for_login,
)

# From battle.net-20260810T230149.081677.log — the session that launched D1.
SUCCESS_LOG = """\
I 2026-08-10 23:01:53.435499 [EnvironmentManager] {Main} Login parameters changed: address=us.actual.battle.net:1119 region=US
I 2026-08-10 23:01:56.334482 [UnifiedAuth] {Main} UAuth: setting url: resources://client/images/icon_error.png reason: missing tassadar url
E 2026-08-10 23:01:56.334511 [UnifiedAuth] {Main} UAuth: tassadar Login URL is empty!
D 2026-08-10 23:01:58.375250 [LoginController] {Main} Login_QueueUpdate (position=1,clientWaitTime=-00:00:00.164386,skipLoginQueueUi=true)
I 2026-08-10 23:01:58.732747 [GSAccountProvider] {Main} Login triggered. entityId=1:0:1278132c
I 2026-08-10 23:01:59.365824 [UnifiedAuth] {Main} UAuth: setting url: https://account.battle.net/login/en/login.app?app=app reason: reset
I 2026-08-10 23:01:59.474197 [BNLogin] {Main} Logged into Battle.net successfully. |bnet=1:0:1278132c|game=2:100417070:71bae85
"""

# From battle.net-20260811T083411.568099.log — the sign-in the tester reported.
FAILED_LOG = """\
E 2026-08-11 08:34:19.298512 [UnifiedAuth] {Main} UAuth: tassadar Login URL is empty!
I 2026-08-11 08:34:21.550097 [LoginController] {Main} External Challenge URL: https://us.account.battle.net/login/?externalChallenge=login&app=app
W 2026-08-11 08:34:21.550149 [LoginController] {Main} Tassadar token rejected by BGS: web_auth_url
I 2026-08-11 08:34:21.597265 [LoginController] {Main} Handling error from service. error=ERROR_TOKEN_NOT_FOUND (49) offline=false interval=00:00:00.000000
E 2026-08-11 08:34:21.914905 [BNLogin] {Main} Login failed. error=ERROR_TOKEN_NOT_FOUND (49)
D 2026-08-11 08:34:22.799753 [UnifiedAuth] {} UAuth: browser state changed: LoginCredential
D 2026-08-11 08:34:24.169175 [UnifiedAuth] {Main} UAuth: finished loading. statusCode=200 state=LoginCredential
"""

# From battle.net-20260811T074540.787819.log — an ``--exec`` handoff.
HANDOFF_LOG = """\
I 2026-08-11 07:45:40.796030 [Main] {Main} Command line arguments: {[0]=--exec=launch D1}
I 2026-08-11 07:45:40.799629 [Main] {Main} Opening IPC shared memory. queueName=User:steamuser:Battle.net IPC ShMem mode=client
I 2026-08-11 07:45:40.800086 [Main] {Main} Leaving because another instance of battle.net is running
D 2026-08-11 07:45:40.800101 [Main] {Main} Shutting down Backend
"""

LOG_SUBDIR = "drive_c/users/steamuser/AppData/Local/Battle.net/Logs"


def _write_log(prefix, name: str, body: str, mtime: float | None = None):
    """Drop a client log into ``prefix``, optionally back-dating it."""
    logs = prefix / LOG_SUBDIR
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / name
    path.write_text(body, encoding="utf-8")
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return path


def test_successful_session_reads_signed_in(tmp_path):
    _write_log(tmp_path, "battle.net-2.log", SUCCESS_LOG)
    assert read_login_state(tmp_path) is LoginState.SIGNED_IN


def test_failed_session_reads_signed_out(tmp_path):
    _write_log(tmp_path, "battle.net-2.log", FAILED_LOG)
    assert read_login_state(tmp_path) is LoginState.SIGNED_OUT


def test_login_url_alone_is_not_signed_out(tmp_path):
    """The success log contains the login URL too — it proves nothing.

    Keying on ``UAuth: setting url: .../login/...`` would have called a
    perfectly signed-in client signed out, and refused every launch.
    """
    assert "account.battle.net/login" in SUCCESS_LOG
    _write_log(tmp_path, "battle.net-2.log", SUCCESS_LOG)
    assert read_login_state(tmp_path) is LoginState.SIGNED_IN


def test_recovery_within_one_session_reads_signed_in(tmp_path):
    """Failed on a stale token, then the user typed their password."""
    _write_log(tmp_path, "battle.net-2.log", FAILED_LOG + SUCCESS_LOG)
    assert read_login_state(tmp_path) is LoginState.SIGNED_IN


def test_exec_handoff_log_is_skipped(tmp_path):
    """The newest log after a phase C is the handoff, and it knows nothing.

    Without this the probe reads a four-line IPC log and answers UNKNOWN for
    an already-running client, which is exactly when the gate is wanted.
    """
    _write_log(tmp_path, "battle.net-1.log", FAILED_LOG, mtime=1000)
    _write_log(tmp_path, "battle.net-2.log", HANDOFF_LOG, mtime=2000)
    assert read_login_state(tmp_path) is LoginState.SIGNED_OUT


def test_newest_real_session_wins(tmp_path):
    """A stale signed-out log must not veto the session running now."""
    _write_log(tmp_path, "battle.net-1.log", FAILED_LOG, mtime=1000)
    _write_log(tmp_path, "battle.net-2.log", SUCCESS_LOG, mtime=2000)
    assert read_login_state(tmp_path) is LoginState.SIGNED_IN


def test_no_logs_is_unknown(tmp_path):
    """Never a verdict without evidence — callers must not block on this."""
    assert read_login_state(tmp_path) is LoginState.UNKNOWN


def test_unreadable_prefix_is_unknown(tmp_path):
    """A prefix with no drive_c at all is UNKNOWN, not signed out."""
    assert read_login_state(tmp_path / "does-not-exist") is LoginState.UNKNOWN


def test_unrecognised_log_is_unknown(tmp_path):
    """A vendor log format change degrades to UNKNOWN rather than failing."""
    _write_log(tmp_path, "battle.net-2.log", "I 2026-08-11 [Main] {Main} hello\n")
    assert read_login_state(tmp_path) is LoginState.UNKNOWN


@pytest.mark.asyncio
async def test_wait_returns_as_soon_as_signed_in(tmp_path):
    _write_log(tmp_path, "battle.net-2.log", SUCCESS_LOG)
    state = await wait_for_login(tmp_path, deadline_seconds=5.0, poll=0.01)
    assert state is LoginState.SIGNED_IN


@pytest.mark.asyncio
async def test_wait_reports_signed_out_only_at_the_deadline(tmp_path):
    """The login page is the user's chance to fix this, so the wait rides it out."""
    _write_log(tmp_path, "battle.net-2.log", FAILED_LOG)
    state = await wait_for_login(tmp_path, deadline_seconds=0.05, poll=0.01)
    assert state is LoginState.SIGNED_OUT


@pytest.mark.asyncio
async def test_wait_picks_up_a_sign_in_that_lands_mid_wait(tmp_path):
    """Signed out at the first poll, signed in by a later one."""
    _write_log(tmp_path, "battle.net-2.log", FAILED_LOG)

    async def _sign_in_soon() -> None:
        import asyncio

        await asyncio.sleep(0.02)
        _write_log(tmp_path, "battle.net-2.log", FAILED_LOG + SUCCESS_LOG)

    import asyncio

    task = asyncio.create_task(_sign_in_soon())
    state = await wait_for_login(tmp_path, deadline_seconds=5.0, poll=0.01)
    await task
    assert state is LoginState.SIGNED_IN


@pytest.mark.asyncio
async def test_wait_returns_at_once_when_there_is_no_log(tmp_path):
    """A prefix with no client log must not cost the caller its whole budget.

    The first cut waited the full deadline on absence of evidence, which
    stalled every launch whose log could not be read by three minutes — a
    worse bug than the one the gate was added for, and it hung the suite.
    """
    import time

    started = time.monotonic()
    state = await wait_for_login(tmp_path, deadline_seconds=60.0, poll=5.0)
    assert state is LoginState.UNKNOWN
    assert time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_wait_gives_up_on_a_verdictless_log_after_the_settle_budget(tmp_path):
    """A log that exists but says nothing decisive is waited on only briefly."""
    _write_log(tmp_path, "battle.net-2.log", "I 2026-08-11 [Main] {Main} hello\n")
    state = await wait_for_login(
        tmp_path, deadline_seconds=10.0, poll=0.01, settle_seconds=0.02,
    )
    assert state is LoginState.UNKNOWN
