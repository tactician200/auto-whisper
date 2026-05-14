"""Tests for auto_whisper.service_lifecycle (Slice 5.1)."""

import importlib
from unittest.mock import MagicMock

import pytest


# --- AUTO_WHISPER_AUTOSTART_SERVICE env parsing ---

@pytest.mark.parametrize("env_value, expected", [
    ("1", True),
    ("0", False),
    ("", True),  # absent → autostart ON (vs explicit "0")
    ("true", False),  # only literal "1" enables — explicit
])
def test_autostart_default_on(monkeypatch, env_value, expected):
    if env_value:
        monkeypatch.setenv("AUTO_WHISPER_AUTOSTART_SERVICE", env_value)
    else:
        monkeypatch.delenv("AUTO_WHISPER_AUTOSTART_SERVICE", raising=False)

    from auto_whisper import service_lifecycle
    importlib.reload(service_lifecycle)
    assert service_lifecycle.is_autostart_enabled() is expected


# --- ensure_service_running paths ---

@pytest.fixture
def fresh_lifecycle(monkeypatch):
    """Reload the module so each test gets a clean _managed_proc=None state.
    Also unregister any lingering atexit hooks from prior tests."""
    import atexit

    from auto_whisper import service_lifecycle

    importlib.reload(service_lifecycle)
    yield service_lifecycle
    # Best-effort cleanup; atexit doesn't expose unregister-by-target reliably
    if service_lifecycle._managed_proc is not None:
        try:
            service_lifecycle._managed_proc.terminate()
        except Exception:
            pass
        service_lifecycle._managed_proc = None


def _patch_health_response(monkeypatch, healthy: bool):
    """Make ServiceClient.health() return the given value, no real HTTP."""
    fake = MagicMock()
    fake.health.return_value = healthy

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return fake

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "auto_whisper.service_client.ServiceClient", FakeClient
    )
    return fake


def test_returns_true_when_service_already_running(fresh_lifecycle, monkeypatch):
    _patch_health_response(monkeypatch, healthy=True)

    spawn_calls = []
    monkeypatch.setattr(
        fresh_lifecycle, "_spawn_service",
        lambda: spawn_calls.append(1) or MagicMock(),
    )

    assert fresh_lifecycle.ensure_service_running() is True
    assert spawn_calls == []  # didn't spawn — service was up


def test_does_not_spawn_when_autostart_disabled(fresh_lifecycle, monkeypatch):
    monkeypatch.setenv("AUTO_WHISPER_AUTOSTART_SERVICE", "0")
    importlib.reload(fresh_lifecycle)

    _patch_health_response(monkeypatch, healthy=False)

    spawn_calls = []
    monkeypatch.setattr(
        fresh_lifecycle, "_spawn_service",
        lambda: spawn_calls.append(1) or MagicMock(),
    )

    assert fresh_lifecycle.ensure_service_running() is False
    assert spawn_calls == []


def test_spawns_when_service_down_and_health_comes_up(fresh_lifecycle, monkeypatch):
    """Initial probe says down → spawn → wait_for_health says up → return True."""
    # Initial probe: unhealthy. _wait_for_health: healthy. Sequence the responses.
    health_results = iter([False, True])
    fake = MagicMock()
    fake.health.side_effect = lambda: next(health_results)

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return fake

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "auto_whisper.service_client.ServiceClient", FakeClient
    )

    fake_proc = MagicMock()
    fake_proc.pid = 99999
    fake_proc.poll.return_value = None  # alive
    monkeypatch.setattr(fresh_lifecycle, "_spawn_service", lambda: fake_proc)

    assert fresh_lifecycle.ensure_service_running(timeout=2.0) is True
    assert fresh_lifecycle._managed_proc is fake_proc


def test_returns_false_when_health_never_comes_up(fresh_lifecycle, monkeypatch):
    """Service was spawned but stays unhealthy → ensure_service_running fails
    cleanly with False (caller logs and continues with degraded experience)."""
    _patch_health_response(monkeypatch, healthy=False)

    fake_proc = MagicMock()
    fake_proc.pid = 88888
    fake_proc.poll.return_value = None
    monkeypatch.setattr(fresh_lifecycle, "_spawn_service", lambda: fake_proc)

    # Use a tiny timeout so the test doesn't actually wait 10s.
    assert fresh_lifecycle.ensure_service_running(timeout=0.1) is False


# --- shutdown hook ---

def test_shutdown_terminates_managed_proc(fresh_lifecycle):
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # still alive
    fresh_lifecycle._managed_proc = fake_proc

    fresh_lifecycle._shutdown()

    fake_proc.terminate.assert_called_once()
    fake_proc.wait.assert_called_once_with(timeout=5)
    assert fresh_lifecycle._managed_proc is None


def test_shutdown_kills_if_terminate_times_out(fresh_lifecycle):
    import subprocess

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=5)
    fresh_lifecycle._managed_proc = fake_proc

    fresh_lifecycle._shutdown()

    fake_proc.terminate.assert_called_once()
    fake_proc.kill.assert_called_once()


def test_shutdown_skips_already_exited_proc(fresh_lifecycle):
    fake_proc = MagicMock()
    fake_proc.poll.return_value = 0  # already exited
    fresh_lifecycle._managed_proc = fake_proc

    fresh_lifecycle._shutdown()

    fake_proc.terminate.assert_not_called()
    assert fresh_lifecycle._managed_proc is None


def test_shutdown_is_safe_when_no_managed_proc(fresh_lifecycle):
    fresh_lifecycle._managed_proc = None
    # Must not raise.
    fresh_lifecycle._shutdown()


# --- _spawn_service ---

def test_watchdog_start_is_idempotent(fresh_lifecycle, monkeypatch):
    """_start_watchdog must spawn at most one thread even if called twice
    (e.g. after a respawn cycle that re-enters ensure_service_running)."""
    started = []
    monkeypatch.setattr(
        fresh_lifecycle.threading,
        "Thread",
        lambda *a, **kw: started.append(MagicMock()) or started[-1],
    )

    fresh_lifecycle._start_watchdog()
    fresh_lifecycle._start_watchdog()

    assert len(started) == 1
    started[0].start.assert_called_once()


def test_watchdog_respawns_dead_proc(fresh_lifecycle, monkeypatch):
    """If the managed subprocess has exited, the watchdog must spawn a
    fresh one and update _managed_proc to point at it."""
    dead_proc = MagicMock()
    dead_proc.pid = 11111
    dead_proc.poll.return_value = 1  # exited with non-zero
    dead_proc.returncode = 1
    fresh_lifecycle._managed_proc = dead_proc

    new_proc = MagicMock()
    new_proc.pid = 22222
    new_proc.poll.return_value = None
    monkeypatch.setattr(fresh_lifecycle, "_spawn_service", lambda: new_proc)
    monkeypatch.setattr(fresh_lifecycle, "_wait_for_health", lambda timeout: True)

    # Break the infinite loop after one iteration by raising on sleep #2.
    sleep_calls = {"n": 0}

    def fake_sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise SystemExit  # bail out cleanly

    monkeypatch.setattr(fresh_lifecycle.time, "sleep", fake_sleep)

    with pytest.raises(SystemExit):
        fresh_lifecycle._watchdog_loop()

    assert fresh_lifecycle._managed_proc is new_proc


def test_spawn_uses_correct_module_argument(fresh_lifecycle, monkeypatch):
    """The subprocess command must invoke `python -m auto_whisper_service.main`
    so the spawned service uses the same logic as `make run-service`."""
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return MagicMock()

    monkeypatch.setattr(
        "auto_whisper.service_lifecycle.subprocess.Popen", fake_popen
    )

    fresh_lifecycle._spawn_service()
    assert "auto_whisper_service.main" in captured["cmd"]
    assert "-m" in captured["cmd"]
    # start_new_session ensures Ctrl+C in daemon doesn't cascade
    assert captured["kw"].get("start_new_session") is True
