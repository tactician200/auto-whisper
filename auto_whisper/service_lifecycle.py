"""Auto-start the local service from the daemon.

Why this exists: until Phase 5.2 ships the LaunchAgent plists, beta users
have to start `auto_whisper_service.main` manually in another terminal
before launching the menubar — too much friction for "I want to try v5
right now". This module probes /health on daemon startup and spawns the
service as a subprocess if nothing is listening yet.

The atexit hook tears the spawned subprocess down when the daemon exits,
so users get a clean Ctrl+C without an orphaned service hanging around.
If the LaunchAgent has already booted the service, the probe finds it,
no spawning happens, and the atexit hook is a no-op.

Opt out with AUTO_WHISPER_AUTOSTART_SERVICE=0. Useful when you want to
debug the service in another terminal with verbose logging while the
menubar talks to it normally.
"""

import atexit
import logging
import os
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)


# Probe-and-spawn budget. Service startup measured ~0.5s on M1; 10s gives
# headroom for cold-start virtualenv + imports on slower machines.
HEALTH_TIMEOUT_S = 10.0
HEALTH_POLL_INTERVAL_S = 0.25

# Watchdog cadence — checks the managed subprocess is alive and healthy.
# 30s is rare enough to not spam logs, frequent enough that a crashed
# service is back before the user notices on the next LLM action.
WATCHDOG_INTERVAL_S = 30.0

_managed_proc: subprocess.Popen | None = None
_watchdog_started = False


def is_autostart_enabled() -> bool:
    """AUTO_WHISPER_AUTOSTART_SERVICE=0 disables the spawn path.

    Default ON because the alternative (silent connection failure when
    flags are ON but the service isn't running) is worse UX than spawning.
    """
    return os.environ.get("AUTO_WHISPER_AUTOSTART_SERVICE", "1") == "1"


def ensure_service_running(timeout: float = HEALTH_TIMEOUT_S) -> bool:
    """Probe /health; if down + autostart enabled, spawn the service.

    Returns True iff the service is reachable when this function exits.
    Idempotent — if the service is already up (e.g. LaunchAgent booted it),
    no spawn happens and we return True immediately.
    """
    from auto_whisper.service_client import ServiceClient

    with ServiceClient(timeout=2.0) as sc:
        if sc.health():
            logger.info("Service already running — connecting to existing instance")
            return True

    if not is_autostart_enabled():
        logger.warning(
            "Service not reachable and AUTO_WHISPER_AUTOSTART_SERVICE=0; "
            "flag-on paths will fail until you start the service manually."
        )
        return False

    logger.info("Service not running — auto-starting subprocess")
    proc = _spawn_service()
    _register_shutdown_hook(proc)

    if not _wait_for_health(timeout):
        logger.error(
            f"Auto-started service (pid={proc.pid}) did not become healthy "
            f"within {timeout}s — flag-on paths will likely fail."
        )
        return False

    logger.info(f"Service auto-started successfully (pid={proc.pid})")
    _start_watchdog()
    return True


def _start_watchdog() -> None:
    """Background thread that respawns the managed service if it dies.

    Only relevant when this process spawned the service itself. If the
    LaunchAgent (or the user) booted it externally, `_managed_proc` is
    None and the watchdog stays idle — restarting that instance is the
    job of whoever owns it.
    """
    global _watchdog_started
    if _watchdog_started:
        return
    _watchdog_started = True
    threading.Thread(target=_watchdog_loop, daemon=True, name="service-watchdog").start()
    logger.info("Service watchdog started (interval=%.0fs)", WATCHDOG_INTERVAL_S)


def _watchdog_loop() -> None:
    from auto_whisper.service_client import ServiceClient

    while True:
        time.sleep(WATCHDOG_INTERVAL_S)
        global _managed_proc
        if _managed_proc is None:
            continue
        if _managed_proc.poll() is None:
            with ServiceClient(timeout=2.0) as sc:
                if sc.health():
                    continue
            logger.warning(
                "Managed service (pid=%s) is alive but /health unreachable — terminating before respawn",
                _managed_proc.pid,
            )
            try:
                _managed_proc.terminate()
                _managed_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _managed_proc.kill()
        else:
            logger.warning(
                "Managed service exited (pid=%s, returncode=%s) — respawning",
                _managed_proc.pid,
                _managed_proc.returncode,
            )

        proc = _spawn_service()
        _managed_proc = proc
        if _wait_for_health(HEALTH_TIMEOUT_S):
            logger.info("Service respawned successfully (pid=%s)", proc.pid)
        else:
            logger.error(
                "Respawned service (pid=%s) did not become healthy within %.0fs",
                proc.pid,
                HEALTH_TIMEOUT_S,
            )


def _service_interpreter_and_env() -> tuple[str, dict]:
    """Pick the right Python + env vars for spawning the service.

    - In a source checkout, `sys.executable` is the venv's `python` which
      has uvicorn / fastapi / etc. on its default sys.path. Return it as-is.
    - In a py2app bundle, `sys.executable` is the bundle launcher binary
      (e.g. AutoWhisper), NOT a Python interpreter. The embedded Python
      lives at `Contents/MacOS/python`, but it's a bare launcher that does
      not auto-add the bundle's `lib/python3.11/` to sys.path. We have to
      do that ourselves via PYTHONPATH, otherwise `import uvicorn` fails.
    """
    if getattr(sys, "frozen", False):
        # Inside a py2app bundle.
        from pathlib import Path
        bundle_macos = Path(sys.executable).parent           # Contents/MacOS
        bundle_python = bundle_macos / "python"
        bundle_libs = bundle_macos.parent / "Resources" / "lib" / "python3.11"
        if bundle_python.exists() and bundle_libs.exists():
            env = os.environ.copy()
            # Prepend so the bundle's site-packages wins over anything the
            # user might have on their system PYTHONPATH.
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                f"{bundle_libs}:{existing}" if existing else str(bundle_libs)
            )
            return str(bundle_python), env
    # Source path — venv has everything we need on its default sys.path.
    return sys.executable, os.environ.copy()


def _spawn_service() -> subprocess.Popen:
    """Spawn `python -m auto_whisper_service.main` as a detached subprocess.

    `start_new_session=True` puts the service in its own process group so
    Ctrl+C in the daemon's terminal goes only to the daemon — the atexit
    hook does the cleanup deliberately, not via signal cascade. Stdout/err
    go to DEVNULL because the service has its own logger; mixing into the
    daemon's stdout causes interleaved log noise.
    """
    interpreter, env = _service_interpreter_and_env()
    cmd = [interpreter, "-m", "auto_whisper_service.main"]
    logger.info("Spawning service: %s", " ".join(cmd))
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )


def _wait_for_health(timeout: float) -> bool:
    """Poll /health until it returns True or the timeout elapses."""
    from auto_whisper.service_client import ServiceClient

    deadline = time.time() + timeout
    while time.time() < deadline:
        with ServiceClient(timeout=1.0) as sc:
            if sc.health():
                return True
        time.sleep(HEALTH_POLL_INTERVAL_S)
    return False


def _register_shutdown_hook(proc: subprocess.Popen) -> None:
    """Remember the spawned subprocess so atexit can terminate it cleanly."""
    global _managed_proc
    _managed_proc = proc
    atexit.register(_shutdown)


def _shutdown() -> None:
    """atexit handler — terminate the managed subprocess if still alive."""
    global _managed_proc
    if _managed_proc is None:
        return
    if _managed_proc.poll() is not None:
        # Already exited (likely service crashed or was killed externally)
        _managed_proc = None
        return
    logger.info(f"Daemon exiting — stopping managed service (pid={_managed_proc.pid})")
    _managed_proc.terminate()
    try:
        _managed_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("Managed service didn't stop in 5s — killing")
        _managed_proc.kill()
    _managed_proc = None
