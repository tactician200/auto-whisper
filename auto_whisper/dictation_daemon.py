#!/usr/bin/env python3
"""
auto-whisper — Live Dictation Daemon

Modes:
  - Cloud (default): Groq whisper-large-v3 API (~100ms latency)
  - Local: whisper.cpp medium model (~6s latency)
  - Auto: Cloud when online, falls back to Local

Double-tap Right Cmd to toggle recording. Or click menu bar icon.
"""

import os
import subprocess
import threading
import time
import logging
import sys
import tempfile
import shutil
import wave
import io
import json
import re
from datetime import datetime
import numpy as np
import sounddevice as sd
import rumps

os.environ.setdefault("LANG", "en_US.UTF-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from pathlib import Path
from AppKit import (
    NSEvent, NSFlagsChangedMask, NSWorkspace,
    NSPasteboard, NSPasteboardTypeString, NSApplication, NSSound,
)
from Quartz import (
    CGEventCreateKeyboardEvent, CGEventSetFlags, CGEventPost,
    kCGHIDEventTap, kCGEventFlagMaskCommand,
)
import ctypes
import ctypes.util

from auto_whisper import __version__
from auto_whisper.dictation_buffer import buffer as _dictation_buffer
from auto_whisper.ui import FloatingHUD
from auto_whisper.transcription import (
    USE_SERVICE_TRANSCRIPTION,
    transcribe_cloud as _transcribe_cloud_routed,
)
from shared.config import (
    WHISPER_BIN, WHISPER_MODEL,
    SAMPLE_RATE, AUTO_WHISPER_LOGS, GROQ_API_KEY_DICTATION,
)
from shared.transcription_cleanup import clean_transcription, normalize_text

AUTO_WHISPER_LOGS.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(AUTO_WHISPER_LOGS / "dictation.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# --- CoreAudio device-change listener (event-driven, zero CPU) ---

_coreaudio_path = ctypes.util.find_library("CoreAudio")
_CoreAudio = ctypes.cdll.LoadLibrary(_coreaudio_path) if _coreaudio_path else None


class _AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


_kAudioObjectSystemObject = 1
_kAudioHardwarePropertyDevices = int.from_bytes(b"dev#", "big")
_kAudioObjectPropertyScopeGlobal = int.from_bytes(b"glob", "big")
_kAudioObjectPropertyElementMain = 0

# OSStatus (*)(AudioObjectID, UInt32, const AudioObjectPropertyAddress*, void*)
_LISTENER_PROC = ctypes.CFUNCTYPE(
    ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint32,
    ctypes.POINTER(_AudioObjectPropertyAddress), ctypes.c_void_p,
)
_active_listeners: list = []  # prevent GC of callback pointers


def _register_device_change_listener(callback) -> bool:
    """Register a CoreAudio listener for device add/remove. Returns True on success."""
    if _CoreAudio is None:
        logger.warning("CoreAudio not found — device hotplug detection disabled")
        return False

    def _listener(_obj_id, _num_addr, _addresses, _client_data):
        try:
            callback()
        except Exception as e:
            logger.error(f"Device change callback error: {e}")
        return 0  # noErr

    proc = _LISTENER_PROC(_listener)
    _active_listeners.append(proc)

    address = _AudioObjectPropertyAddress(
        _kAudioHardwarePropertyDevices,
        _kAudioObjectPropertyScopeGlobal,
        _kAudioObjectPropertyElementMain,
    )
    status = _CoreAudio.AudioObjectAddPropertyListener(
        _kAudioObjectSystemObject, ctypes.byref(address), proc, None,
    )
    if status != 0:
        logger.warning(f"AudioObjectAddPropertyListener failed (status {status})")
        return False
    logger.info("CoreAudio device-change listener registered")
    return True


FRAMES_PER_BUFFER = 1024
RIGHT_CMD_KEYCODE = 54
LEFT_CMD_KEYCODE = 55
OPTION_FLAG_MASK = 1 << 19  # NSEventModifierFlagOption
SHIFT_FLAG_MASK = 1 << 17   # NSEventModifierFlagShift — reserved (currently unused)
DOUBLE_TAP_WINDOW = 0.4
V_KEYCODE = 9
C_KEYCODE = 8
PASTEBOARD_RESTORE_DELAY = 0.6

# No sentence prompt — only vocabulary hints to avoid hallucination on long audio
WHISPER_PROMPT = None
MAX_RECORDING_SECONDS = 300  # 5 min auto-stop guard
SILENCE_AUTOSTOP_SECONDS = 8.0  # auto-stop after N seconds of silence
SILENCE_RMS_THRESHOLD = 0.004  # below this RMS = silence (0.004 tolerates AirPods low amplitude)
STREAM_OPEN_TIMEOUT = 5.0  # max seconds for sd.InputStream() constructor
PORTAUDIO_RESET_TIMEOUT = 5.0  # max seconds for sd._terminate()/_initialize()
WATCHDOG_INTERVAL = 10  # seconds between watchdog checks
WATCHDOG_STUCK_THRESHOLD = 60  # seconds of stuck processing before force-restart
CALLBACK_HEARTBEAT_TIMEOUT = 8.0  # no audio callback for this long → device lost
SOUND_START_DICTATE = "Tink"
SOUND_START_ORGANIZE = "Glass"
SOUND_STOP_RECORDING = "Pop"

# Shared Groq client (thread-safe singleton)
from shared.groq_client import get_groq_client as _get_groq_client
_injection_lock = threading.Lock()
_sound_cache = {}

# Transcription modes
MODE_AUTO = "Auto"
MODE_CLOUD = "Cloud (Groq)"
MODE_LOCAL = "Local"
INPUT_SYSTEM_DEFAULT = "System Default"

RECORDING_MODE_DICTATE = "dictate"   # R⌘⌘ — raw transcription, no LLM
RECORDING_MODE_ORGANIZE = "organize" # menu-only explicit organize
RECORDING_MODE_ROUTE = "route"       # Opt+R⌘⌘ — smart-dictation: voice command → action, else prompt

# Output modes
OUTPUT_SPEAK = "Speak"
OUTPUT_PASTE = "Paste"

# Language modes
LANG_AUTO = "Auto-detect"
LANG_ES = "Español"
LANG_EN = "English"
LANG_MAP = {LANG_AUTO: None, LANG_ES: "es", LANG_EN: "en"}


# --- Sound feedback ---

def play_sound(name: str):
    """Play macOS system sound (thread-safe, non-blocking)."""
    sound_path = f"/System/Library/Sounds/{name}.aiff"
    try:
        sound = _sound_cache.get(name)
        if sound is None:
            sound = NSSound.soundNamed_(name)
            if sound is None and Path(sound_path).exists():
                sound = NSSound.alloc().initWithContentsOfFile_byReference_(sound_path, True)
            if sound is not None:
                _sound_cache[name] = sound

        if sound is not None:
            sound.stop()
            sound.play()
            return
    except Exception as e:
        logger.warning(f"Failed to play AppKit sound '{name}': {e}")

    try:
        subprocess.Popen(
            ["/usr/bin/afplay", sound_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.warning(f"Failed to play sound '{name}': {e}")


def preload_sounds(*names: str):
    for name in names:
        try:
            if name in _sound_cache:
                continue
            sound = NSSound.soundNamed_(name)
            sound_path = f"/System/Library/Sounds/{name}.aiff"
            if sound is None and Path(sound_path).exists():
                sound = NSSound.alloc().initWithContentsOfFile_byReference_(sound_path, True)
            if sound is not None:
                _sound_cache[name] = sound
        except Exception:
            pass


# --- Internet check ---

def is_online() -> bool:
    """Quick check if internet is available (DNS resolve)."""
    import socket
    try:
        conn = socket.create_connection(("api.groq.com", 443), timeout=1.5)
        conn.close()
        return True
    except OSError:
        return False


# --- Permission checks ---

def check_accessibility() -> bool:
    import ApplicationServices
    return ApplicationServices.AXIsProcessTrustedWithOptions(
        {ApplicationServices.kAXTrustedCheckOptionPrompt: True}
    )


# --- Focus management ---

def get_frontmost_app():
    return NSWorkspace.sharedWorkspace().frontmostApplication()


def is_own_app(app) -> bool:
    try:
        return bool(app) and app.processIdentifier() == os.getpid()
    except Exception:
        return False


def restore_focus(app):
    if not app:
        return
    try:
        app.activateWithOptions_(2)
        for _ in range(20):
            time.sleep(0.05)
            current = NSWorkspace.sharedWorkspace().frontmostApplication()
            if current and current.processIdentifier() == app.processIdentifier():
                break
        time.sleep(0.1)
    except Exception as e:
        logger.warning(f"Could not restore focus: {e}")


# --- Text capture (selected text via Cmd+C) ---

def capture_selected_text() -> str | None:
    """Simulate Cmd+C and read clipboard. Returns selected text or None."""
    with _injection_lock:
        board = NSPasteboard.generalPasteboard()
        old_count = board.changeCount()
        old_content = board.stringForType_(NSPasteboardTypeString)

        try:
            # Simulate Cmd+C
            c_down = CGEventCreateKeyboardEvent(None, C_KEYCODE, True)
            c_up = CGEventCreateKeyboardEvent(None, C_KEYCODE, False)
            CGEventSetFlags(c_down, kCGEventFlagMaskCommand)
            CGEventSetFlags(c_up, kCGEventFlagMaskCommand)
            CGEventPost(kCGHIDEventTap, c_down)
            CGEventPost(kCGHIDEventTap, c_up)
            time.sleep(0.15)

            # Check if clipboard changed
            if board.changeCount() == old_count:
                return None

            text = board.stringForType_(NSPasteboardTypeString)
            return text.strip() if text else None
        finally:
            # Preserve the user's clipboard when capture is triggered by a hotkey.
            if old_content is not None and board.changeCount() != old_count:
                time.sleep(0.05)
                board.clearContents()
                board.setString_forType_(old_content, NSPasteboardTypeString)


def read_clipboard_text() -> str | None:
    """Read the current clipboard text directly (no Cmd+C, no mutation).

    Used by router actions that take a payload (translate/reply) when the
    dictation is instruction-only — the content to act on lives in the clipboard.
    """
    board = NSPasteboard.generalPasteboard()
    text = board.stringForType_(NSPasteboardTypeString)
    return text.strip() if text and text.strip() else None


# Leading connector after a translate/tone verb, e.g. "a inglés", "al francés",
# "to english", "en formal". Removed to isolate the content to transform.
_LEADING_CONNECTOR = re.compile(r"^(?:a|al|en|hacia|to|into)\s+\S+\s*", re.IGNORECASE)
_LEADING_FILLER = re.compile(
    r"^(?:esto|eso|lo siguiente|el texto|el mensaje|por favor)\b[\s,:;.\-]*",
    re.IGNORECASE,
)


def _drop_until_keyword(s: str, keywords, max_lead_words: int = 3) -> str:
    """Drop everything up to and including the first `keyword` that appears
    within the first `max_lead_words` words.

    This absorbs any filler between the verb and the language/tone word, in any
    order — "tradúcelo esto al inglés X" and "tradúcemelo al inglés X" both
    reduce to "X". The connector/filler regexes miss these because the extra
    tokens ("esto", "lo", "porfa") aren't a known connector OR filler. The
    word-position guard avoids eating real content that merely mentions the
    keyword later ("no me gusta el inglés británico" stays intact).
    """
    low = s.lower()
    best_end = None
    for kw in keywords:
        m = re.search(rf"\b{re.escape(kw)}\b", low)
        if m and len(low[:m.start()].split()) <= max_lead_words:
            if best_end is None or m.end() < best_end:
                best_end = m.end()
    if best_end is not None:
        return s[best_end:].lstrip(" ,:;.-¿?¡!")
    return s


def _strip_leading_instruction(text: str, action) -> str:
    """Best-effort: drop the canonical verb + the instruction prefix from the
    phrase start, leaving the content the user wants transformed.

    "traduce a inglés tengo una reunión"   → "tengo una reunión"
    "tradúcelo esto al inglés tengo prisa"  → "tengo prisa"
    "ponlo más formal oye dame eso"         → "oye dame eso"
    Heuristic, not a parser — when in doubt it leaves text intact.
    """
    if not text or action is None:
        return (text or "").strip()
    s = text.strip()
    low = s.lower()
    for verb in sorted(action.canonical_verbs, key=len, reverse=True):
        if low.startswith(verb):
            s = s[len(verb):]
            break
    s = s.lstrip(" ,:;.-¿?¡!")
    # Primary, robust cut: the instruction ends at the language word (translate)
    # or the tone word (tone). Dropping up to it absorbs intervening filler.
    aid = getattr(action, "id", None)
    if aid == "translate":
        from shared.intent_router import _LANG_MAP
        s = _drop_until_keyword(s, _LANG_MAP.keys())
    elif aid == "tone":
        from shared.intent_router import _TONES
        s = _drop_until_keyword(s, _TONES)
    # Fallback cleanup when no keyword matched (e.g. a verb with no language word).
    s = _LEADING_CONNECTOR.sub("", s, count=1)
    s = _LEADING_FILLER.sub("", s, count=1)
    return s.strip()


def _resolve_payload(action_id: str, raw_text: str, clipboard: str | None):
    """Decide what text a router action operates on. Returns (payload, instruction).

    - reply:           payload = clipboard message, instruction = the dictation
    - translate/tone:  payload = dictation minus the leading instruction; if that
                       is too short and the action takes a payload, use the
                       clipboard; otherwise fall back to the raw dictation.
    Pure function — no I/O — so it's unit-testable; the daemon passes the
    clipboard in.
    """
    import shared.voice_actions as _va
    action = _va.get(action_id)
    if action_id == "reply":
        return (clipboard or "", raw_text)
    content = _strip_leading_instruction(raw_text, action)
    if action is not None and action.needs_payload:
        if len(content.split()) >= 3:
            return (content, "")
        if clipboard:
            return (clipboard, "")
        return (content or raw_text, "")
    # tone (no payload): use stripped content when it's substantial, else raw.
    return (content if len(content.split()) >= 2 else raw_text, "")


def _wait_for_frontmost_app(app, timeout: float = 1.0) -> bool:
    """Wait briefly until the requested app is frontmost."""
    if not app:
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        current = NSWorkspace.sharedWorkspace().frontmostApplication()
        if current and current.processIdentifier() == app.processIdentifier():
            return True
        time.sleep(0.05)
    return False


# --- Text injection ---

def inject_text(text: str, target_app=None, blocking: bool = False):
    if not text or not text.strip():
        return
    text = text.strip()

    def _do_inject():
        with _injection_lock:
            board = NSPasteboard.generalPasteboard()
            old_content = board.stringForType_(NSPasteboardTypeString)
            try:
                paste_sent = False
                for attempt in range(3):
                    if target_app:
                        restore_focus(target_app)
                        if not _wait_for_frontmost_app(target_app, timeout=1.2):
                            logger.warning(
                                f"Target app did not regain focus before paste attempt {attempt + 1}"
                            )

                    board.clearContents()
                    board.setString_forType_(text, NSPasteboardTypeString)
                    time.sleep(0.08)

                    event_down = CGEventCreateKeyboardEvent(None, V_KEYCODE, True)
                    event_up = CGEventCreateKeyboardEvent(None, V_KEYCODE, False)
                    CGEventSetFlags(event_down, kCGEventFlagMaskCommand)
                    CGEventSetFlags(event_up, kCGEventFlagMaskCommand)
                    CGEventPost(kCGHIDEventTap, event_down)
                    CGEventPost(kCGHIDEventTap, event_up)
                    time.sleep(0.24 + attempt * 0.12)

                    current = NSWorkspace.sharedWorkspace().frontmostApplication()
                    if not target_app or (
                        current and current.processIdentifier() == target_app.processIdentifier()
                    ):
                        paste_sent = True
                        logger.info(f"Paste shortcut sent ({len(text)} chars)")
                        break

                if paste_sent:
                    play_sound("Tink")
                else:
                    logger.warning("Paste shortcut may have missed the target app")
                    play_sound("Basso")
            finally:
                time.sleep(PASTEBOARD_RESTORE_DELAY)
                if old_content is not None:
                    board.clearContents()
                    board.setString_forType_(old_content, NSPasteboardTypeString)

    t = threading.Thread(target=_do_inject, daemon=True)
    t.start()
    if blocking:
        t.join(timeout=10.0)


def resample_audio(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample mono audio array from orig_sr to target_sr using linear interpolation."""
    if orig_sr == target_sr:
        return audio
    target_len = int(len(audio) * target_sr / orig_sr)
    orig_indices = np.linspace(0, len(audio) - 1, target_len)
    return np.interp(orig_indices, np.arange(len(audio)), audio).astype(np.float32)


def list_input_devices() -> list[tuple[str, int]]:
    try:
        hostapis = sd.query_hostapis()
        devices = []
        for index, device in enumerate(sd.query_devices()):
            if int(device.get("max_input_channels", 0) or 0) < 1:
                continue
            hostapi_name = ""
            hostapi_index = device.get("hostapi")
            if isinstance(hostapi_index, int) and 0 <= hostapi_index < len(hostapis):
                hostapi_name = hostapis[hostapi_index].get("name", "")
            label = device.get("name", f"Input {index}")
            if hostapi_name:
                label = f"{label} [{hostapi_name}]"
            devices.append((label, index))
        return devices
    except Exception as e:
        logger.warning(f"Could not enumerate input devices: {e}")
        return []


# --- Transcription: Cloud (Groq, possibly via service) ---

def transcribe_cloud(audio_data: np.ndarray, language: str | None = "es") -> str | None:
    """Cloud transcription — routes via service if USE_SERVICE_TRANSCRIPTION,
    else direct Groq. WHISPER_PROMPT is honored only on the direct path
    (service vocabulary hint plumbing is Phase 2.4)."""
    return _transcribe_cloud_routed(
        audio_data,
        sample_rate=SAMPLE_RATE,
        language=language,
        whisper_prompt=WHISPER_PROMPT,
    )


# --- Transcription: Local (whisper.cpp) ---

WHISPER_ARTIFACTS = {"[BLANK_AUDIO]", "[Music]", "[Applause]", "[Silence]", "(Silence)", "(Music)"}


def transcribe_local(audio_data: np.ndarray, language: str | None = "es") -> str | None:
    """Transcribe via local whisper.cpp. ~6-8s latency."""
    from shared.whisper_runner import transcribe_wav

    tmpdir = tempfile.mkdtemp()
    try:
        wav_path = os.path.join(tmpdir, "audio.wav")
        with wave.open(wav_path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            pcm = (audio_data * 32767).astype(np.int16)
            wf.writeframes(pcm.tobytes())

        t0 = time.time()
        text = transcribe_wav(
            Path(wav_path),
            model=WHISPER_MODEL,
            language=language,
            beam_size=8,
            entropy_thold=2.4,
            no_timestamps=True,
            prompt=WHISPER_PROMPT,
            timeout=120,
        )
        elapsed = time.time() - t0
        logger.info(f"Local transcription: {elapsed:.1f}s")

        if text and text not in WHISPER_ARTIFACTS:
            return clean_transcription(text) or None
        return None
    except Exception as e:
        logger.error(f"Local transcription failed: {e}")
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --- Usage tracker ---

class UsageTracker:
    """Track daily Groq API usage against free tier limits."""
    DAILY_AUDIO_LIMIT = 28800  # seconds (8 hours) — Groq free tier
    DAILY_REQUEST_LIMIT = 2000
    STATE_PATH = AUTO_WHISPER_LOGS / "usage_tracker.json"
    LOG_USAGE_RE = re.compile(r"Groq usage: Usage:\s+.*?(\d+)/(\d+)min")

    def __init__(self):
        self._lock = threading.Lock()
        self._date = None
        self.audio_seconds = 0.0
        self.requests = 0
        self._load_state()
        self._reset_if_new_day()

    def _today_key(self) -> str:
        from datetime import date
        return date.today().isoformat()

    def _load_state(self):
        try:
            if self.STATE_PATH.exists():
                data = json.loads(self.STATE_PATH.read_text(encoding="utf-8"))
                self._date = data.get("date")
                self.audio_seconds = float(data.get("audio_seconds", 0.0) or 0.0)
                self.requests = int(data.get("requests", 0) or 0)
                return
        except Exception as e:
            logger.warning(f"Could not load usage tracker state: {e}")

        self._load_today_from_logs()

    def _load_today_from_logs(self):
        today_key = self._today_key()
        log_path = AUTO_WHISPER_LOGS / "dictation.log"
        self._date = today_key
        self.audio_seconds = 0.0
        self.requests = 0

        if not log_path.exists():
            return

        try:
            max_used_minutes = 0
            request_count = 0
            for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line.startswith(today_key):
                    continue
                match = self.LOG_USAGE_RE.search(line)
                if not match:
                    continue
                request_count += 1
                max_used_minutes = max(max_used_minutes, int(match.group(1)))

            self.audio_seconds = float(max_used_minutes * 60)
            self.requests = request_count
            if max_used_minutes or request_count:
                logger.info(
                    f"Usage tracker restored from logs: {max_used_minutes}min, {request_count} cloud requests"
                )
                self._save_state_locked()
        except Exception as e:
            logger.warning(f"Could not rebuild usage tracker from logs: {e}")

    def _save_state_locked(self):
        payload = {
            "date": self._date,
            "audio_seconds": self.audio_seconds,
            "requests": self.requests,
        }
        tmp_path = self.STATE_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        tmp_path.replace(self.STATE_PATH)

    def _reset_if_new_day(self):
        today = self._today_key()
        if self._date != today:
            self._date = today
            self.audio_seconds = 0.0
            self.requests = 0
            self._save_state_locked()

    def record(self, audio_duration: float):
        with self._lock:
            self._reset_if_new_day()
            self.audio_seconds += audio_duration
            self.requests += 1
            self._save_state_locked()

    @property
    def audio_pct(self) -> float:
        with self._lock:
            self._reset_if_new_day()
            return min(self.audio_seconds / self.DAILY_AUDIO_LIMIT * 100, 100)

    @property
    def remaining_minutes(self) -> float:
        with self._lock:
            self._reset_if_new_day()
            return max((self.DAILY_AUDIO_LIMIT - self.audio_seconds) / 60, 0)

    def format_bar(self) -> str:
        """Return a compact usage bar for the menu."""
        with self._lock:
            self._reset_if_new_day()
            pct = min(self.audio_seconds / self.DAILY_AUDIO_LIMIT * 100, 100)
            used_min = self.audio_seconds / 60
        CELLS = 5
        filled = pct / 100 * CELLS
        full = int(filled)
        half = 1 if (filled - full) >= 0.5 else 0
        empty = CELLS - full - half
        bar = "◼" * full + ("◻" if half else "") + "·" * empty
        return f"API: {bar} {used_min:.0f}m · {pct:.0f}%"

    @property
    def is_near_limit(self) -> bool:
        return self.audio_pct >= 80

    @property
    def is_over_limit(self) -> bool:
        return self.audio_pct >= 100


usage_tracker = UsageTracker()


# --- Smart transcription router ---

MAX_CHUNK_SECONDS = 55  # Whisper degrades past ~60s, chunk at 55 with overlap
CHUNK_OVERLAP_SECONDS = 2  # overlap to avoid cutting words


def transcribe_chunked(audio_data: np.ndarray, mode: str, language: str | None = "es") -> tuple[str | None, str]:
    """Split long audio into chunks, transcribe each, concatenate."""
    chunk_size = MAX_CHUNK_SECONDS * SAMPLE_RATE
    overlap = CHUNK_OVERLAP_SECONDS * SAMPLE_RATE
    results = []
    engine_used = "none"
    start = 0

    total_chunks = max(1, int(np.ceil(len(audio_data) / (chunk_size - overlap))))
    logger.info(f"Chunking: {total_chunks} segments of ~{MAX_CHUNK_SECONDS}s")

    while start < len(audio_data):
        end = min(start + chunk_size, len(audio_data))
        chunk = audio_data[start:end]

        # Skip near-silent chunks
        rms = np.sqrt(np.mean(chunk ** 2))
        if rms < 0.001:
            start += chunk_size - overlap
            continue

        text, engine = transcribe_audio(chunk, mode, language=language)
        engine_used = engine
        if text:
            results.append(text.strip())
        start += chunk_size - overlap

    if results:
        return " ".join(results), engine_used
    return None, engine_used


def transcribe_audio(audio_data: np.ndarray, mode: str, language: str | None = "es") -> tuple[str | None, str]:
    """Route transcription based on mode. Returns (text, engine_used)."""
    duration = len(audio_data) / SAMPLE_RATE
    use_cloud = mode in (MODE_CLOUD, MODE_AUTO)

    # Privacy mode overrides everything: no network calls regardless of the
    # user's engine pick. Logged at info so it's obvious from the daemon log
    # why a Groq-selected session ended up on local.
    from shared.user_profile import is_privacy_mode
    if use_cloud and is_privacy_mode():
        logger.info("Privacy mode on → forcing local transcription")
        use_cloud = False

    if use_cloud and usage_tracker.is_over_limit:
        logger.warning(f"Daily Groq limit reached ({usage_tracker.audio_seconds:.0f}s). Using local.")
        use_cloud = False

    if use_cloud and mode == MODE_AUTO:
        use_cloud = GROQ_API_KEY_DICTATION and is_online()

    if use_cloud:
        text = transcribe_cloud(audio_data, language=language)
        if text:
            usage_tracker.record(duration)
            logger.info(f"Groq usage: {usage_tracker.format_bar()}")
            return text, "groq"
        logger.warning("Cloud failed, trying local fallback...")

    text = transcribe_local(audio_data, language=language)
    engine = "local" if mode == MODE_LOCAL else "local (fallback)"
    return text, engine


def _looks_like_spurious_short_transcript(text: str, duration: float, rms: float) -> bool:
    normalized = normalize_text(text)
    suspicious_short_outputs = {
        "gracias",
        "muchas gracias",
        "gracias gracias",
        "hola",
        "hola buenos dias",
    }
    if normalized not in suspicious_short_outputs:
        return False

    word_count = len(normalized.split())
    return duration <= 2.5 and rms < 0.0065 and word_count <= 3


# --- Menu bar app ---

class AutoWhisperApp(rumps.App):
    ICON_IDLE = "◎"
    ICON_STARTING = "◠"
    ICON_RECORDING = "◉"
    ICON_PROCESSING = "⟳"
    ICON_SPEAKING = "◈"

    @property
    def recording(self):
        return self._recording_event.is_set()

    @recording.setter
    def recording(self, value):
        if value:
            self._recording_event.set()
        else:
            self._recording_event.clear()

    def __init__(self):
        super().__init__("auto-whisper", quit_button="Quit")
        # Force accessory/menu-bar behavior even when launched directly via python.
        NSApplication.sharedApplication().setActivationPolicy_(1)
        # Always boot with privacy OFF so Groq is the default engine on every
        # launch. Users opt into privacy from Settings → Privacy each session.
        os.environ["AUTO_WHISPER_PRIVACY_MODE"] = "0"
        self._recording_event = threading.Event()
        self._processing = False
        self._processing_start = 0
        self.audio_frames = []
        self._frames_lock = threading.Lock()
        self._recording_lock = threading.Lock()
        self._processing_lock = threading.Lock()
        self.stream = None
        self._target_app = None
        self._monitor = None
        self._record_start_time = None
        self._capture_sample_rate = SAMPLE_RATE  # may differ if device needs native SR
        self.title = self.ICON_IDLE
        # Lazy: NSStatusItem button is not built yet (rumps creates it in run()).
        # Controller fetches it on first apply().
        from auto_whisper.ui.title_pulse import TitlePulseController
        self._title_pulse = TitlePulseController(self)
        self._last_rcmd_time = 0
        self._rcmd_was_down = False
        self._last_lcmd_time = 0
        self._lcmd_was_down = False
        self._last_transcription = None
        self._speaking_process = None
        self._last_voice_time = time.time()
        self._silence_stop_fired = False
        self._max_duration_stop_fired = False
        self._recording_mode = RECORDING_MODE_DICTATE
        self._last_paste_target = None
        self._input_device_index = None
        self._input_device_label = INPUT_SYSTEM_DEFAULT
        self._record_thread = None  # track recording thread to detect hangs
        self._last_callback_time = 0  # heartbeat timestamp from audio callback
        preload_sounds(
            SOUND_START_DICTATE,
            SOUND_START_ORGANIZE,
            SOUND_STOP_RECORDING,
            "Tink",
            "Basso",
            "Funk",
        )

        try:
            self._hud = FloatingHUD()
        except Exception as exc:
            self._hud = None
            print(f"[ui] FloatingHUD init failed: {exc}", flush=True)

        # Default mode
        self._mode = MODE_CLOUD if GROQ_API_KEY_DICTATION else MODE_LOCAL
        self._language = LANG_ES
        self._output_mode = OUTPUT_SPEAK

        # Engine submenu
        self._mode_cloud = rumps.MenuItem(MODE_CLOUD, callback=self._set_mode)
        self._mode_local = rumps.MenuItem(MODE_LOCAL, callback=self._set_mode)
        self._mode_auto = rumps.MenuItem(MODE_AUTO, callback=self._set_mode)
        self._update_mode_checks()

        # Language submenu
        self._lang_auto = rumps.MenuItem(LANG_AUTO, callback=self._set_language)
        self._lang_es = rumps.MenuItem(LANG_ES, callback=self._set_language)
        self._lang_en = rumps.MenuItem(LANG_EN, callback=self._set_language)
        self._update_lang_checks()

        # Input device submenu
        self._input_items: dict[str, rumps.MenuItem] = {}
        self._input_system_default = rumps.MenuItem(INPUT_SYSTEM_DEFAULT, callback=self._set_input_device)
        self._input_items[INPUT_SYSTEM_DEFAULT] = self._input_system_default
        self._input_device_options = [(INPUT_SYSTEM_DEFAULT, None), *list_input_devices()]
        self._input_menu_items = [self._input_system_default]
        for label, index in self._input_device_options[1:]:
            item = rumps.MenuItem(label, callback=self._set_input_device)
            self._input_menu_items.append(item)
            self._input_items[label] = item
        self._input_refresh = rumps.MenuItem("↻ Refresh devices", callback=self._on_refresh_input)
        self._input_menu_items.append(None)  # separator
        self._input_menu_items.append(self._input_refresh)
        self._update_input_checks()

        # Auto-detect audio device changes (hotplug/unplug)
        self._setup_audio_device_listener()

        # Header: dynamic engine·lang status + usage bar. Static version line
        # was removed — it lived above and read like noise on every open.
        self._status_item = rumps.MenuItem(self._format_status_line())
        self._usage_item = rumps.MenuItem(usage_tracker.format_bar())

        # Hotkey hint — replaces the now-removed "Dictate" primary button so
        # demo users discover the gesture without an extra click target.
        self._btn_hotkey_hint = rumps.MenuItem("Hold ⌘⌘ to dictate")
        self._btn_hotkey_hint.set_callback(None)  # display-only

        # Primary actions operate on the last clipboard copy — phrasing
        # "last copy" tested clearer than "this text" with non-technical
        # users (no ambiguity about which text the action targets).
        self._btn_summarize = rumps.MenuItem("")
        self._btn_summarize.set_callback(self._menu_summarize)
        self._btn_explain = rumps.MenuItem("")
        self._btn_explain.set_callback(self._menu_explain)
        self._btn_organize_text = rumps.MenuItem("Organize last copy")
        self._btn_organize_text.set_callback(self._menu_organize_text)
        self._btn_optimize_text = rumps.MenuItem("Optimize last copy → prompt")
        self._btn_optimize_text.set_callback(self._menu_optimize_text)
        self._btn_read = rumps.MenuItem("Read last copy aloud")
        self._btn_read.set_callback(self._menu_read)
        self._btn_paste_last = rumps.MenuItem("Paste last again")
        self._btn_paste_last.set_callback(None)  # disabled until first transcription

        # Reformat last — when the classifier picked the wrong path. Operates
        # on the raw transcript stored in the dictation buffer (not the
        # processed paste, since re-processing processed text degrades).
        self._btn_reformat_coding  = rumps.MenuItem("As coding prompt")
        self._btn_reformat_coding.set_callback(None)  # enabled after first dict
        self._btn_reformat_writing = rumps.MenuItem("As writing")
        self._btn_reformat_writing.set_callback(None)
        self._btn_reformat_research = rumps.MenuItem("As research brief")
        self._btn_reformat_research.set_callback(None)
        self._btn_reformat_decision = rumps.MenuItem("As decision brief")
        self._btn_reformat_decision.set_callback(None)
        self._btn_reformat_organize = rumps.MenuItem("As organize")
        self._btn_reformat_organize.set_callback(None)
        self._btn_reformat_raw = rumps.MenuItem("Raw (re-paste original)")
        self._btn_reformat_raw.set_callback(None)

        # Voice modes — two entry points only. The classifier collapses what
        # used to be 3 explicit modes (dictate / organize / optimize) into
        # the smart path.
        self._btn_dictate = rumps.MenuItem("Dictate (raw)")
        self._btn_dictate.set_callback(self._menu_toggle)
        self._btn_route = rumps.MenuItem("Dictate Smart (Opt+R⌘⌘)")
        self._btn_route.set_callback(self._menu_route)
        # Kept for internal compatibility (no menu entry, but used as a
        # recording-mode constant; the classifier can still resolve to it).
        self._btn_organize = rumps.MenuItem("")
        self._btn_organize.set_callback(self._menu_organize)

        # Output controls (toggle + stop) — relocated to Settings since
        # they are rarely flipped per-session.
        self._btn_stop = rumps.MenuItem("Stop speaking")
        self._btn_stop.set_callback(self._menu_stop_speaking)
        self._btn_output_toggle = rumps.MenuItem(self._output_toggle_label())
        self._btn_output_toggle.set_callback(self._toggle_output_mode)

        # Privacy toggle — flips AUTO_WHISPER_PRIVACY_MODE at runtime so
        # users can switch from the menu without restarting / editing env.
        self._btn_privacy_toggle = rumps.MenuItem(self._privacy_toggle_label())
        self._btn_privacy_toggle.set_callback(self._toggle_privacy_mode)

        # ── Vocabulary controls ──
        # Project label is updated dynamically by _refresh_project_title.
        from auto_whisper.transcription import ACTIVE_PROJECT
        self._btn_project = rumps.MenuItem(self._format_project_label(ACTIVE_PROJECT))
        self._btn_project.set_callback(self._menu_set_project)
        self._btn_add_term = rumps.MenuItem("Add to dictionary…")
        self._btn_add_term.set_callback(self._menu_add_term)

        # ── Recent dictations submenu ──
        # Submenu items are populated dynamically by _refresh_recent_menu.
        self._btn_recent = rumps.MenuItem("Recent dictations")
        self._refresh_recent_menu()  # initial render (likely empty)

        self._update_action_titles()

        self.menu = [
            # ── Header (dynamic state + usage) ────────────────────────────
            self._status_item,
            self._usage_item,
            None,
            # ── Hotkey hint ───────────────────────────────────────────────
            self._btn_hotkey_hint,
            None,
            # ── Reformat last (only enabled once a dictation exists) ─────
            [rumps.MenuItem("Reformat last…"), [
                self._btn_reformat_coding,
                self._btn_reformat_writing,
                self._btn_reformat_research,
                self._btn_reformat_decision,
                self._btn_reformat_organize,
                self._btn_reformat_raw,
            ]],
            None,
            # ── Process clipboard (operates on copied text, not voice) ────
            [rumps.MenuItem("Process clipboard…"), [
                self._btn_optimize_text,
                self._btn_organize_text,
                self._btn_summarize,
                self._btn_explain,
                self._btn_read,
            ]],
            None,
            # ── Recent dictations + repeat ────────────────────────────────
            self._btn_recent,
            self._btn_paste_last,
            None,
            # ── Top-level toggle: privacy lives outside Settings because
            # it's flipped often enough to deserve one-click reach.
            self._btn_privacy_toggle,
            # ── Settings (everything secondary lives here) ────────────────
            [rumps.MenuItem("Settings"), [
                [rumps.MenuItem("Voice modes"), [
                    self._btn_dictate,
                    self._btn_route,
                ]],
                [rumps.MenuItem("Engine"), [self._mode_cloud, self._mode_local, self._mode_auto]],
                [rumps.MenuItem("Language"), [self._lang_es, self._lang_en, self._lang_auto]],
                [rumps.MenuItem("Input"), self._input_menu_items],
                self._btn_output_toggle,
                [rumps.MenuItem("Vocabulary"), [
                    self._btn_project,
                    self._btn_add_term,
                ]],
                self._btn_stop,
            ]],
        ]
        self._setup_hotkey()

        # Watchdog: detects stuck states and forces recovery
        threading.Thread(target=self._watchdog_loop, daemon=True, name="watchdog").start()

    def _format_status_line(self):
        engine_short = {"Cloud (Groq)": "Cloud", "Local": "Local", "Auto": "Auto"}.get(self._mode, self._mode)
        lang_short = {"Español": "ES", "English": "EN", "Auto-detect": "Auto"}.get(self._language, "?")
        return f"{engine_short} · {lang_short}"

    def _set_mode(self, sender):
        self._mode = sender.title
        self._update_mode_checks()
        self._set_ui(self.title, self._format_status_line())
        logger.info(f"Mode changed to: {self._mode}")

    def _update_mode_checks(self):
        self._mode_cloud.state = self._mode == MODE_CLOUD
        self._mode_local.state = self._mode == MODE_LOCAL
        self._mode_auto.state = self._mode == MODE_AUTO

    def _set_language(self, sender):
        self._language = sender.title
        self._update_lang_checks()
        self._set_ui(self.title, self._format_status_line())
        logger.info(f"Language changed to: {self._language}")

    def _update_lang_checks(self):
        self._lang_auto.state = self._language == LANG_AUTO
        self._lang_es.state = self._language == LANG_ES
        self._lang_en.state = self._language == LANG_EN

    def _output_toggle_label(self) -> str:
        # Single-line status — clicking toggles. "Output: Speak" reads as
        # current state, mirroring how macOS Settings rows phrase toggles.
        current = "Speak" if self._output_mode == OUTPUT_SPEAK else "Paste"
        return f"Output: {current}"

    def _update_action_titles(self):
        """Update action titles to reflect current output mode."""
        mode = "speak" if self._output_mode == OUTPUT_SPEAK else "paste"
        self._btn_summarize.title = f"Summarize last copy → {mode}"
        self._btn_explain.title = f"Explain last copy → {mode}"

    def _toggle_output_mode(self, _):
        self._output_mode = OUTPUT_PASTE if self._output_mode == OUTPUT_SPEAK else OUTPUT_SPEAK
        self._btn_output_toggle.title = self._output_toggle_label()
        self._update_action_titles()
        logger.info(f"Output mode: {self._output_mode}")

    def _privacy_toggle_label(self) -> str:
        from shared.user_profile import is_privacy_mode
        # Label is the row's current state — same phrasing pattern as
        # _output_toggle_label. "Local" reminds the user that privacy
        # forces local-only routing (no Groq, no Edge TTS).
        state = "On (local)" if is_privacy_mode() else "Off"
        return f"Privacy: {state}"

    def _toggle_privacy_mode(self, _):
        # Privacy is env-var driven; flip in-place and refresh the label.
        # Persists for the daemon's lifetime; LaunchAgent restart resets
        # to whatever the plist sets (currently nothing → starts Off).
        from shared.user_profile import is_privacy_mode
        new_value = "0" if is_privacy_mode() else "1"
        os.environ["AUTO_WHISPER_PRIVACY_MODE"] = new_value
        self._btn_privacy_toggle.title = self._privacy_toggle_label()
        logger.info(f"Privacy mode: {'on' if new_value == '1' else 'off'}")

    def _refresh_input_devices(self):
        """Re-enumerate audio input devices and rebuild the Input submenu."""
        new_devices = list_input_devices()
        self._input_device_options = [(INPUT_SYSTEM_DEFAULT, None), *new_devices]

        # Check if currently selected device still exists
        if self._input_device_index is not None:
            still_exists = any(
                idx == self._input_device_index for _, idx in new_devices
            )
            if not still_exists:
                logger.warning(
                    f"Previously selected device '{self._input_device_label}' "
                    f"(index {self._input_device_index}) no longer available, "
                    f"resetting to {INPUT_SYSTEM_DEFAULT}"
                )
                self._input_device_index = None
                self._input_device_label = INPUT_SYSTEM_DEFAULT

        # Rebuild submenu items
        input_menu = self.menu.get("Input")
        if input_menu:
            input_menu.clear()
            self._input_items = {}

            # System Default
            self._input_system_default = rumps.MenuItem(
                INPUT_SYSTEM_DEFAULT, callback=self._set_input_device
            )
            self._input_items[INPUT_SYSTEM_DEFAULT] = self._input_system_default
            input_menu[INPUT_SYSTEM_DEFAULT] = self._input_system_default

            # Device items — use unique keys to handle duplicate names
            for label, index in new_devices:
                menu_key = f"{label}||{index}"
                item = rumps.MenuItem(label, callback=self._set_input_device)
                item._aw_device_index = index  # store index directly on the item
                self._input_items[menu_key] = item
                input_menu[menu_key] = item

            # Separator + refresh button
            input_menu["_sep"] = None
            self._input_refresh = rumps.MenuItem(
                "↻ Refresh devices", callback=self._on_refresh_input
            )
            input_menu["↻ Refresh devices"] = self._input_refresh

        self._update_input_checks()
        logger.info(f"Input devices refreshed: {len(new_devices)} found")

    def _on_refresh_input(self, _):
        self._refresh_input_devices()
        self._set_ui(self.title, f"Devices refreshed ({len(self._input_device_options) - 1})")

    def _set_input_device(self, sender):
        label = sender.title
        if label == INPUT_SYSTEM_DEFAULT:
            self._input_device_index = None
            self._input_device_label = INPUT_SYSTEM_DEFAULT
        else:
            # Use stored device index if available (handles duplicate names)
            device_index = getattr(sender, '_aw_device_index', None)
            if device_index is not None:
                self._input_device_label = label
                self._input_device_index = device_index
            else:
                # Fallback: search by label in options list
                selected = next(
                    ((dl, di) for dl, di in self._input_device_options if dl == label),
                    None,
                )
                if selected is None:
                    self._set_ui(self.title, "Input device unavailable")
                    play_sound("Basso")
                    return
                self._input_device_label = selected[0]
                self._input_device_index = selected[1]

        self._update_input_checks()
        self._set_ui(self.title, self._format_status_line())
        logger.info(f"Input device changed to: {self._input_device_label} (index: {self._input_device_index})")

    def _update_input_checks(self):
        for key, item in self._input_items.items():
            if key == INPUT_SYSTEM_DEFAULT:
                item.state = self._input_device_index is None
            else:
                device_index = getattr(item, '_aw_device_index', None)
                item.state = (device_index is not None and device_index == self._input_device_index)

    def _setup_audio_device_listener(self):
        """Register CoreAudio listener for automatic hotplug/unplug detection."""
        self._device_change_last = 0.0

        def _on_device_change():
            # Debounce: CoreAudio fires multiple events per single device change
            now = time.time()
            if now - self._device_change_last < 2.0:
                return
            self._device_change_last = now
            logger.info("Audio device change detected (CoreAudio notification)")
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self._handle_device_change)

        if _register_device_change_listener(_on_device_change):
            logger.info("Automatic audio device detection enabled")

    def _handle_device_change(self):
        """Called on main thread when CoreAudio reports device add/remove."""
        # Mark HUD panel stale — empirically, an audio device change followed
        # by a delayed show (10+s later) sometimes leaves the panel in a
        # state where orderFrontRegardless silently no-ops. Rebuild on next show.
        if self._hud is not None:
            try:
                self._hud.mark_stale("audio device change")
            except Exception:
                pass

        old_index = self._input_device_index
        old_label = self._input_device_label
        self._refresh_input_devices()
        device_lost = old_index is not None and self._input_device_index is None
        if device_lost:
            self._set_ui(self.title, f"'{old_label}' disconnected → System Default")
            logger.warning(f"Device '{old_label}' (index {old_index}) removed, switched to System Default")
            # If actively recording on the removed device, stop gracefully and
            # transcribe what we have rather than losing the audio.
            if self.recording:
                logger.warning("Device removed during recording — stopping and transcribing captured audio")
                threading.Thread(target=self._locked_stop_and_transcribe, daemon=True).start()
        else:
            logger.info(f"Device list updated ({len(self._input_device_options) - 1} devices)")

        # PortAudio caches the default input device and does not invalidate it
        # when CoreAudio reports a change. Without this reset, sd.query_devices()
        # keeps returning the vanished device, causing PaErrorCode -9986 on the
        # next InputStream (e.g. AirPods removed → default still points at them).
        def _reset_after_settle():
            deadline = time.time() + 5.0
            while (self.recording or self._processing) and time.time() < deadline:
                time.sleep(0.2)
            self._safe_portaudio_reset("after device change")
        threading.Thread(target=_reset_after_settle, daemon=True).start()

    def _create_stream_with_timeout(self, device, samplerate):
        """Create sd.InputStream with timeout — CoreAudio can block indefinitely."""
        result = [None]
        error = [None]
        def _create():
            try:
                result[0] = sd.InputStream(
                    device=device,
                    samplerate=samplerate, channels=1,
                    dtype="float32", blocksize=FRAMES_PER_BUFFER,
                    callback=self._audio_callback,
                )
            except Exception as e:
                error[0] = e
        t = threading.Thread(target=_create, daemon=True)
        t.start()
        t.join(timeout=STREAM_OPEN_TIMEOUT)
        if t.is_alive():
            logger.error(f"InputStream creation timed out ({STREAM_OPEN_TIMEOUT}s) — CoreAudio stuck")
            self._safe_portaudio_reset("after InputStream timeout")
            raise TimeoutError(f"InputStream creation timed out ({STREAM_OPEN_TIMEOUT}s)")
        if error[0]:
            raise error[0]
        return result[0]

    def _safe_portaudio_reset(self, context=""):
        """Reset PortAudio with timeout. If it deadlocks, force-restart via launchd."""
        label = f" ({context})" if context else ""
        def _reset():
            sd._terminate()
            sd._initialize()
        t = threading.Thread(target=_reset, daemon=True)
        t.start()
        t.join(timeout=PORTAUDIO_RESET_TIMEOUT)
        if t.is_alive():
            logger.critical(f"PortAudio reset deadlocked{label} — forcing restart via launchd")
            os._exit(1)
        logger.info(f"PortAudio reset completed{label}")
        from PyObjCTools import AppHelper
        AppHelper.callAfter(self._refresh_input_devices)

    def _watchdog_loop(self):
        """Background watchdog — detect stuck states and force recovery."""
        while True:
            time.sleep(WATCHDOG_INTERVAL)
            now = time.time()

            # Check 1: Processing stuck for too long → force-restart
            if self._processing and self._processing_start:
                stuck = now - self._processing_start
                if stuck > WATCHDOG_STUCK_THRESHOLD:
                    logger.critical(f"Watchdog: processing stuck for {stuck:.0f}s — forcing restart")
                    os._exit(1)

            # Check 2: Recording but no audio callbacks → device likely lost
            if (self.recording and self._record_start_time
                    and self._last_callback_time > 0):
                since_callback = now - self._last_callback_time
                since_start = now - self._record_start_time
                if (since_start > CALLBACK_HEARTBEAT_TIMEOUT
                        and since_callback > CALLBACK_HEARTBEAT_TIMEOUT):
                    logger.warning(
                        f"Watchdog: no audio callback for {since_callback:.1f}s "
                        f"— device likely lost, auto-stopping"
                    )
                    play_sound("Basso")
                    self._last_callback_time = now  # prevent re-trigger
                    threading.Thread(
                        target=self._locked_stop_and_transcribe, daemon=True
                    ).start()

    def _remember_paste_target(self, app):
        if app and not is_own_app(app):
            self._last_paste_target = app

    def _resolve_paste_target(self):
        current = get_frontmost_app()
        if current and not is_own_app(current):
            self._remember_paste_target(current)
            return current
        if self._target_app and not is_own_app(self._target_app):
            return self._target_app
        if self._last_paste_target and not is_own_app(self._last_paste_target):
            return self._last_paste_target
        return None

    def _paste_last(self, _):
        """Re-paste the last transcription."""
        if self._last_transcription:
            target_app = self._resolve_paste_target()
            if target_app is None:
                self._set_ui(self.ICON_IDLE, "No target app for paste")
                play_sound("Basso")
                return
            self._set_ui(self.ICON_IDLE, "Pasting last...")
            inject_text(self._last_transcription, target_app=target_app)
        else:
            self._set_ui(self.ICON_IDLE, "No previous transcription")

    # --- Reformat last dictation ---

    def _get_last_raw(self) -> str | None:
        """Return the raw transcript of the most recent dictation.

        Reformat operates on the *raw* (pre-LLM) transcript so we can route
        the original intent through a different template, instead of feeding
        already-processed text back into an LLM (which degrades quickly).
        """
        try:
            recent = _dictation_buffer.recent(1)
            if recent:
                return recent[0].raw_text
        except Exception:
            pass
        return None

    def _reformat_last(self, action_id: str) -> None:
        """Run a worker that re-processes the last raw transcript as
        action_id ∈ {raw, organize, prompt_coding, prompt_writing,
        research, decision_making}."""
        raw = self._get_last_raw()
        if not raw:
            self._set_ui(self.ICON_IDLE, "No previous dictation")
            play_sound("Basso")
            return
        target_app = self._resolve_paste_target()
        if target_app is None:
            self._set_ui(self.ICON_IDLE, "No target app for paste")
            play_sound("Basso")
            return

        def _worker():
            from shared.user_profile import is_privacy_mode
            output_text = raw
            status_prefix = "Re-pasted"
            llm_needed = action_id in (
                "organize", "prompt_coding", "prompt_writing",
                "research", "decision_making",
            )
            if llm_needed and is_privacy_mode():
                logger.info("Privacy on — reformat %s → raw", action_id)
                status_prefix = "Re-pasted raw (privacy)"
            elif action_id == "organize":
                self._set_ui(self.ICON_PROCESSING, "Reformatting (organize)...")
                from auto_whisper.text_processor import organize_ideas
                try:
                    result = organize_ideas(raw)
                except Exception as e:
                    logger.error("Reformat organize failed: %s", e)
                    result = None
                if result and result.strip():
                    output_text = result.strip()
                    status_prefix = "Reformatted (organize)"
            elif action_id == "prompt_coding":
                self._set_ui(self.ICON_PROCESSING, "Reformatting (coding prompt)...")
                from auto_whisper.text_processor import optimize_prompt
                try:
                    result = optimize_prompt(raw)
                except Exception as e:
                    logger.error("Reformat coding failed: %s", e)
                    result = None
                if result and result.strip():
                    output_text = result.strip()
                    status_prefix = "Reformatted (coding)"
            elif action_id == "prompt_writing":
                self._set_ui(self.ICON_PROCESSING, "Reformatting (writing)...")
                from auto_whisper.text_processor import optimize_writing
                try:
                    result = optimize_writing(raw)
                except Exception as e:
                    logger.error("Reformat writing failed: %s", e)
                    result = None
                if result and result.strip():
                    output_text = result.strip()
                    status_prefix = "Reformatted (writing)"
            elif action_id == "research":
                self._set_ui(self.ICON_PROCESSING, "Reformatting (research)...")
                from auto_whisper.text_processor import research_brief
                try:
                    result = research_brief(raw)
                except Exception as e:
                    logger.error("Reformat research failed: %s", e)
                    result = None
                if result and result.strip():
                    output_text = result.strip()
                    status_prefix = "Reformatted (research)"
            elif action_id == "decision_making":
                self._set_ui(self.ICON_PROCESSING, "Reformatting (decision)...")
                from auto_whisper.text_processor import decision_brief
                try:
                    result = decision_brief(raw)
                except Exception as e:
                    logger.error("Reformat decision failed: %s", e)
                    result = None
                if result and result.strip():
                    output_text = result.strip()
                    status_prefix = "Reformatted (decision)"

            self._last_transcription = output_text
            from PyObjCTools import AppHelper
            def _finish():
                inject_text(output_text, target_app=target_app, blocking=False)
                self._set_ui(self.ICON_IDLE, f"✓ {status_prefix}")
            AppHelper.callAfter(_finish)

        threading.Thread(target=_worker, daemon=True, name="reformat").start()

    def _menu_reformat_coding(self, _):   self._reformat_last("prompt_coding")
    def _menu_reformat_writing(self, _):  self._reformat_last("prompt_writing")
    def _menu_reformat_research(self, _): self._reformat_last("research")
    def _menu_reformat_decision(self, _): self._reformat_last("decision_making")
    def _menu_reformat_organize(self, _): self._reformat_last("organize")
    def _menu_reformat_raw(self, _):      self._reformat_last("raw")

    # --- Text processing actions ---

    def _process_selection(self, action: str, use_hotkey: bool = False, paste_output: bool = False):
        """
        Process text: summarize/read/explain.
        use_hotkey=True: simulate Cmd+C to capture selection (from hotkey)
        use_hotkey=False: read clipboard as-is (from menu, focus already lost)
        paste_output=True: inject result at cursor instead of speaking it
        """
        if not self._processing_lock.acquire(blocking=False):
            from auto_whisper.voice_agent import is_speaking, stop_speaking
            if is_speaking():
                stop_speaking()
            logger.info(f"Already processing, ignoring {action}")
            return

        # Capture paste target now, before the thread steals focus
        target_app = self._resolve_paste_target() if paste_output else None

        def _do():
            result_text = None
            try:
                # 1. Get text
                if use_hotkey:
                    text = capture_selected_text()
                else:
                    board = NSPasteboard.generalPasteboard()
                    text = board.stringForType_(NSPasteboardTypeString)

                if not text or not text.strip():
                    self._set_ui(self.ICON_IDLE, "No text — copy first (⌘C)")
                    play_sound("Basso")
                    return

                text = text.strip()
                logger.info(f"[{action}{'→paste' if paste_output else ''}] Processing {len(text)} chars...")

                # 2. Process
                if action == "read":
                    result_text = text
                else:
                    self._set_ui(self.ICON_PROCESSING, f"{action.capitalize()}...")
                    play_sound("Glass")
                    from auto_whisper.text_processor import summarize, explain, organize_ideas, optimize_prompt
                    if action == "summarize":
                        result_text = summarize(text)
                    elif action == "organize_text":
                        result_text = organize_ideas(text)
                    elif action == "optimize_text":
                        result_text = optimize_prompt(text)
                    else:
                        result_text = explain(text, for_voice=not paste_output)

                if not result_text:
                    self._set_ui(self.ICON_IDLE, "Processing failed")
                    return

                if paste_output:
                    self._last_transcription = result_text
                    self._btn_paste_last.set_callback(self._paste_last)
                    self._set_ui(self.ICON_PROCESSING, "Pasting...")
                else:
                    self._set_ui(self.ICON_SPEAKING, "Speaking...")
            finally:
                self._processing_lock.release()

            # Output happens outside the lock — can be stopped/interrupted anytime
            if not result_text:
                return
            if paste_output:
                inject_text(result_text, target_app=target_app)
                self._set_ui(self.ICON_IDLE, f"✓ Pasted ({action})")
            else:
                try:
                    from auto_whisper.voice_agent import speak
                    speak(result_text)
                finally:
                    self._set_ui(self.ICON_IDLE, "Done")

        threading.Thread(target=_do, daemon=True).start()

    def _menu_summarize(self, _):
        paste = self._output_mode == OUTPUT_PASTE
        logger.info(f"Menu: Summarize clipboard ({'paste' if paste else 'speak'})")
        self._process_selection("summarize", use_hotkey=False, paste_output=paste)

    def _menu_route(self, _):
        logger.info("Menu: Dictate Smart → intent router")
        self._toggle_recording(RECORDING_MODE_ROUTE)

    def _menu_optimize_text(self, _):
        logger.info("Menu: Optimize clipboard text → prompt")
        self._process_selection("optimize_text", use_hotkey=False, paste_output=True)

    def _menu_organize(self, _):
        logger.info("Menu: Organize ideas")
        self._toggle_recording(RECORDING_MODE_ORGANIZE)

    def _menu_read(self, _):
        logger.info("Menu: Read clipboard")
        self._process_selection("read", use_hotkey=False)

    def _menu_explain(self, _):
        paste = self._output_mode == OUTPUT_PASTE
        logger.info(f"Menu: Explain clipboard ({'paste' if paste else 'speak'})")
        self._process_selection("explain", use_hotkey=False, paste_output=paste)

    def _menu_organize_text(self, _):
        paste = self._output_mode == OUTPUT_PASTE
        logger.info(f"Menu: Organize text ({'paste' if paste else 'speak'})")
        self._process_selection("organize_text", use_hotkey=False, paste_output=paste)

    # --- Vocabulary UI ---

    @staticmethod
    def _format_project_label(project: str | None) -> str:
        """Title shown in the menu for the active project line."""
        return f"Project: {project}" if project else "Project: (none)"

    def _refresh_project_title(self) -> None:
        """Update the project menu item to reflect current ACTIVE_PROJECT.
        Safe to call from any thread that holds the AppKit main thread reference
        (rumps menu items dispatch their title changes appropriately)."""
        from auto_whisper import transcription
        self._btn_project.title = self._format_project_label(transcription.ACTIVE_PROJECT)

    def _menu_set_project(self, _):
        """Prompt for new project name; '' clears, Cancel aborts."""
        from auto_whisper import transcription

        current = transcription.ACTIVE_PROJECT or ""
        win = rumps.Window(
            title="Active project",
            message=(
                "Used to scope vocabulary entries. Leave blank for global vocab only.\n"
                "Effective immediately for next dictation."
            ),
            default_text=current,
            ok="Set",
            cancel="Cancel",
            dimensions=(280, 22),
        )
        resp = win.run()
        if not resp.clicked:
            return

        new_value = resp.text.strip() or None
        transcription.set_active_project(new_value)
        self._refresh_project_title()
        logger.info(f"Active project set to: {new_value!r}")

    # --- Recent dictations submenu ---

    @staticmethod
    def _format_recent_entry_title(entry) -> str:
        """One-line label for a buffer entry: HH:MM + 60-char preview."""
        # entry.timestamp is ISO 8601 UTC; show just HH:MM in the user's local tz
        try:
            dt = datetime.fromisoformat(entry.timestamp)
            hhmm = dt.astimezone().strftime("%H:%M")
        except Exception:
            hhmm = "—:—"
        preview = entry.raw_text.replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:57] + "…"
        return f"{hhmm}  {preview}"

    def _refresh_recent_menu(self) -> None:
        """Rebuild the Recent dictations submenu from current buffer state.
        Safe to call from any thread (rumps handles AppKit dispatch)."""
        try:
            self._btn_recent.clear()
        except Exception:
            # rumps clear may not exist on first call before menu is wired;
            # ignore and let initial render happen during __init__.
            pass

        entries = _dictation_buffer.recent()
        if not entries:
            placeholder = rumps.MenuItem("(no dictations yet)")
            try:
                self._btn_recent.add(placeholder)
            except Exception:
                pass
            return

        for entry in entries:
            parent = rumps.MenuItem(self._format_recent_entry_title(entry))

            paste_raw = rumps.MenuItem("Paste raw")
            # Bind entry id via default-arg trick to avoid late-binding bug.
            paste_raw.set_callback(
                lambda _, eid=entry.id: self._paste_buffer_entry(eid, variant=None)
            )
            parent.add(paste_raw)

            for mode in entry.processed:
                item = rumps.MenuItem(f"Paste as {mode}")
                item.set_callback(
                    lambda _, eid=entry.id, m=mode: self._paste_buffer_entry(eid, variant=m)
                )
                parent.add(item)

            try:
                self._btn_recent.add(parent)
            except Exception as e:
                logger.warning(f"Could not append recent entry to menu: {e}")

    def _paste_buffer_entry(self, entry_id: str, variant: str | None) -> None:
        """Paste action for a Recent dictations submenu item.

        variant=None → paste raw_text; otherwise paste processed[variant].
        """
        entry = _dictation_buffer.get(entry_id)
        if entry is None:
            logger.warning(f"Buffer entry {entry_id} not found (may have been evicted)")
            return

        if variant is None:
            text = entry.raw_text
            label = "raw"
        else:
            text = entry.processed.get(variant)
            label = f"as {variant}"
            if not text:
                logger.warning(f"Buffer entry {entry_id} has no '{variant}' variant")
                return

        target = self._resolve_paste_target()
        logger.info(f"Pasting recent entry ({label}): {text[:60]}...")
        inject_text(text, target_app=target)

    # --- end recent ---

    def _menu_add_term(self, _):
        """Two-step prompt: term, then variants. Saves to vocab DB."""
        from auto_whisper import transcription
        from shared.vocab import VocabManager, get_default_db_path

        # Step 1: canonical term
        win_term = rumps.Window(
            title="Add term to dictionary",
            message=(
                "Step 1/2: canonical spelling (the form you want to appear)."
            ),
            default_text="",
            ok="Next",
            cancel="Cancel",
            dimensions=(280, 22),
        )
        resp_term = win_term.run()
        if not resp_term.clicked:
            return
        term = (resp_term.text or "").strip()
        if not term:
            rumps.alert(title="Empty term", message="Term cannot be empty.")
            return

        # Step 2: variants
        win_var = rumps.Window(
            title=f"Variants for '{term}'",
            message=(
                "Step 2/2: comma-separated mistranscriptions Whisper produces.\n"
                "Leave blank if you only want the term in the prompt hint."
            ),
            default_text="",
            ok="Save",
            cancel="Cancel",
            dimensions=(320, 22),
        )
        resp_var = win_var.run()
        if not resp_var.clicked:
            return

        variants = [
            v.strip() for v in (resp_var.text or "").split(",") if v.strip()
        ]

        # Save to DB (scoped to current ACTIVE_PROJECT)
        try:
            vm = VocabManager(get_default_db_path())
            vm.add_term(
                term=term,
                variants=variants,
                project=transcription.ACTIVE_PROJECT,
                language=None,  # daemon doesn't track per-entry language yet
            )
        except Exception as e:
            logger.error(f"Add term failed: {e}")
            rumps.alert(
                title="Could not add term",
                message=f"{e}",
            )
            return

        scope = f"project '{transcription.ACTIVE_PROJECT}'" if transcription.ACTIVE_PROJECT else "global vocab"
        rumps.alert(
            title="Term added",
            message=(
                f"'{term}' saved to {scope} with "
                f"{len(variants)} variant{'s' if len(variants) != 1 else ''}.\n\n"
                f"Will be applied to subsequent dictations."
            ),
        )
        logger.info(f"Vocab: added '{term}' (project={transcription.ACTIVE_PROJECT}, {len(variants)} variants)")

    def _menu_stop_speaking(self, _):
        from auto_whisper.voice_agent import is_speaking, stop_speaking
        if is_speaking():
            stop_speaking()
            logger.info("Menu: Stopped speaking")
            self._set_ui(self.ICON_IDLE, "Stopped")
        elif self.recording:
            logger.info("Menu: Stopping recording")
            self._toggle_recording()
        else:
            logger.info("Menu: Stop clicked but nothing active")

    # --- Hotkeys ---

    def _setup_hotkey(self):
        def handler(event):
            try:
                kc = event.keyCode()
                flags = event.modifierFlags()

                # Right ⌘ — recording: dictate (none) / smart-dictation router (+Option)
                if kc == RIGHT_CMD_KEYCODE:
                    rcmd_down = bool(flags & (1 << 4))
                    if rcmd_down and not self._rcmd_was_down:
                        now = time.time()
                        if now - self._last_rcmd_time < DOUBLE_TAP_WINDOW:
                            option_held = bool(flags & OPTION_FLAG_MASK)
                            if option_held:
                                # Smart-dictation: intent router (replaces legacy
                                # OPTIMIZE on this hotkey; OPTIMIZE stays on its menu).
                                mode, tag = RECORDING_MODE_ROUTE, "+Opt"
                            else:
                                mode, tag = RECORDING_MODE_DICTATE, ""
                            logger.info(f"Double-tap Right ⌘{tag} → {mode}")
                            self._toggle_recording(mode)
                            self._last_rcmd_time = 0
                        else:
                            self._last_rcmd_time = now
                    self._rcmd_was_down = rcmd_down

                # Left ⌘ — TTS: read (no modifier) / explain (+Option); double-tap while speaking stops
                elif kc == LEFT_CMD_KEYCODE:
                    lcmd_down = bool(flags & kCGEventFlagMaskCommand)
                    if lcmd_down and not self._lcmd_was_down:
                        now = time.time()
                        if now - self._last_lcmd_time < DOUBLE_TAP_WINDOW:
                            from auto_whisper.voice_agent import is_speaking, stop_speaking
                            if is_speaking():
                                logger.info("Double-tap Left ⌘ → stop speaking")
                                stop_speaking()
                                self._set_ui(self.ICON_IDLE, "Stopped")
                            else:
                                option_held = bool(flags & OPTION_FLAG_MASK)
                                action = "explain" if option_held else "read"
                                logger.info(f"Double-tap Left ⌘{'+Opt' if option_held else ''} → {action}")
                                play_sound("Tink")
                                self._process_selection(action, use_hotkey=True, paste_output=False)
                            self._last_lcmd_time = 0
                        else:
                            self._last_lcmd_time = now
                    self._lcmd_was_down = lcmd_down

            except Exception as e:
                logger.error(f"Hotkey handler error: {e}")

        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSFlagsChangedMask, handler
        )
        logger.info("Hotkeys: R⌘⌘=dictate, Opt+R⌘⌘=optimize, Shift+R⌘⌘=voice router, L⌘⌘=read, Opt+L⌘⌘=explain")

    def _toggle_recording(self, recording_mode: str = RECORDING_MODE_DICTATE):
        with self._recording_lock:
            if self.recording:
                # If requesting a different mode while recording, stop current first
                if recording_mode != self._recording_mode:
                    logger.info(f"Switching from {self._recording_mode} to {recording_mode}")
                    self._stop_and_transcribe()
                    # Don't auto-start new mode — let current transcription finish
                else:
                    self._stop_and_transcribe()
            elif self._processing:
                logger.info("Ignoring toggle — still processing")
                self._set_ui(self.title, "Still processing...")
            else:
                self._start_recording(recording_mode)

    def _start_recording(self, recording_mode: str = RECORDING_MODE_DICTATE):
        if self.recording:
            return
        if self._processing:
            # Safety: force-clear if stuck for >30s
            if self._processing_start and time.time() - self._processing_start > 30:
                logger.warning("Force-clearing stuck _processing flag (>30s)")
                self._processing = False
            else:
                logger.info("Ignoring start — still processing")
                return

        # If a previous record thread is still alive (hung InputStream), reset PortAudio
        if self._record_thread is not None and self._record_thread.is_alive():
            logger.warning("Previous record thread still alive — resetting PortAudio")
            self._safe_portaudio_reset("before new recording")

        self._recording_mode = recording_mode
        with self._frames_lock:
            self.audio_frames = []
        now = time.time()
        self._record_start_time = None
        self._last_voice_time = now
        self._silence_stop_fired = False
        self._max_duration_stop_fired = False
        self._capture_sample_rate = SAMPLE_RATE  # safe default; record() updates if device needs native SR
        self.recording = True
        self._target_app = self._resolve_paste_target()
        start_label = "Starting mic..." if recording_mode == RECORDING_MODE_DICTATE else "Starting organizer..."
        self._set_ui(self.ICON_STARTING, start_label)
        from PyObjCTools import AppHelper
        AppHelper.callAfter(lambda: setattr(self._btn_dictate, 'title', '■ Stop dictation'))
        # Show HUD optimistically — Bluetooth mic handshake (AirPods HFP/SCO)
        # can stall the stream open for 1–3s or time out. Without this, the
        # HUD only appears after stream.start() succeeds and looks broken on
        # slow/failed opens. mark_recording_started() flips REC on later.
        if self._hud is not None:
            try:
                from shared.user_profile import is_privacy_mode
                self._hud.show(
                    mode=self._recording_mode,
                    privacy=is_privacy_mode(),
                )
            except Exception as exc:
                print(f"[ui] HUD show failed: {exc}", flush=True)
        target_name = self._target_app.localizedName() if self._target_app else "unknown"
        start_sound = SOUND_START_ORGANIZE if recording_mode == RECORDING_MODE_ORGANIZE else SOUND_START_DICTATE
        # Play the cue immediately so the user gets confirmation even if the
        # input stream takes a moment to initialize.
        play_sound(start_sound)
        logger.info(
            f"Initializing mic... (target: {target_name}, mode: {self._mode}, action: {recording_mode})"
        )

        # Capture device selection before entering the background thread
        initial_device_index = self._input_device_index
        initial_device_label = self._input_device_label

        def record():
            try:
                # Apply Bluetooth → built-in override BEFORE opening the stream.
                # macOS forces AirPods into HFP/SCO when the mic is opened,
                # capping audio at 8-16 kHz mSBC and tanking Whisper accuracy.
                # User-selected devices bypass this (resolve returns their pick).
                from auto_whisper.audio_routing import resolve_input_device
                stream_device, route_reason = resolve_input_device(initial_device_index)

                try:
                    input_device = (
                        sd.query_devices(stream_device, kind="input")
                        if stream_device is not None
                        else sd.query_devices(kind="input")
                    )
                except Exception as device_error:
                    if stream_device is None:
                        logger.warning(f"Could not inspect default input device: {device_error}")
                        raise
                    logger.warning(
                        f"Selected input unavailable ({initial_device_label}); "
                        f"falling back to {INPUT_SYSTEM_DEFAULT}: {device_error}"
                    )
                    stream_device = None
                    route_reason = f"{INPUT_SYSTEM_DEFAULT} (fallback after device unavailable)"
                    self._input_device_index = None
                    self._input_device_label = INPUT_SYSTEM_DEFAULT
                    # UI mutations must happen on the main thread
                    from PyObjCTools import AppHelper
                    AppHelper.callAfter(self._update_input_checks)
                    input_device = sd.query_devices(kind="input")

                logger.info(
                    f"Input device: {input_device['name']} "
                    f"(route: {route_reason}, default SR {input_device['default_samplerate']:.0f} Hz)"
                )

                # Bail out if recording was cancelled while we were setting up
                if not self.recording:
                    logger.info("Recording cancelled before stream creation")
                    self._set_ui(self.ICON_IDLE, self._format_status_line())
                    from PyObjCTools import AppHelper
                    AppHelper.callAfter(lambda: setattr(self._btn_dictate, 'title', 'Dictate'))
                    if self._hud is not None:
                        try:
                            self._hud.hide()
                        except Exception:
                            pass
                    return

                # Try to open at target SR; if device rejects it, fall back to native SR
                native_sr = int(input_device.get("default_samplerate", SAMPLE_RATE))
                capture_sr = SAMPLE_RATE
                try:
                    self.stream = self._create_stream_with_timeout(stream_device, SAMPLE_RATE)
                except TimeoutError:
                    raise  # CoreAudio stuck — don't retry at native SR
                except Exception as sr_error:
                    if native_sr == SAMPLE_RATE:
                        raise
                    logger.warning(
                        f"Cannot open stream at {SAMPLE_RATE} Hz "
                        f"({sr_error}); retrying at native {native_sr} Hz"
                    )
                    capture_sr = native_sr
                    self.stream = self._create_stream_with_timeout(stream_device, native_sr)

                # Bail out if cancelled during stream creation (prevents orphaned streams)
                if not self.recording:
                    logger.info("Recording cancelled after stream creation — closing stream")
                    try:
                        self.stream.close()
                    except Exception:
                        pass
                    self.stream = None
                    self._set_ui(self.ICON_IDLE, self._format_status_line())
                    from PyObjCTools import AppHelper
                    AppHelper.callAfter(lambda: setattr(self._btn_dictate, 'title', 'Dictate'))
                    if self._hud is not None:
                        try:
                            self._hud.hide()
                        except Exception:
                            pass
                    return

                self._capture_sample_rate = capture_sr
                started_at = time.time()
                self._record_start_time = started_at
                self._last_voice_time = started_at
                self.stream.start()
                status = "Recording..." if recording_mode == RECORDING_MODE_DICTATE else "Recording ideas..."
                self._set_ui(self.ICON_RECORDING, status)
                logger.info(f"Recording started ({recording_mode}) at {capture_sr} Hz")
                if self._hud is not None:
                    try:
                        self._hud.mark_recording_started()
                    except Exception as exc:
                        print(f"[ui] HUD mark_recording_started failed: {exc}", flush=True)
            except Exception as e:
                logger.error(f"Failed to start recording: {e}")
                self.recording = False
                self._set_ui(self.ICON_IDLE, "Mic error")
                from PyObjCTools import AppHelper
                AppHelper.callAfter(lambda: setattr(self._btn_dictate, 'title', 'Dictate'))
                if self._hud is not None:
                    try:
                        self._hud.hide()
                    except Exception:
                        pass

        t = threading.Thread(target=record, daemon=True)
        self._record_thread = t
        t.start()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            logger.warning(f"PortAudio callback status: {status}")
        if self.recording:
            with self._frames_lock:
                self.audio_frames.append(indata.copy())

            now = time.time()
            self._last_callback_time = now
            rms = float(np.sqrt(np.mean(indata ** 2)))

            if self._hud is not None:
                try:
                    self._hud.push_level(rms)
                except Exception:
                    pass  # never crash audio thread

            # Track last non-silent audio
            if rms >= SILENCE_RMS_THRESHOLD:
                self._last_voice_time = now

            # Auto-stop on prolonged silence (only after some speech was captured)
            elapsed = now - self._record_start_time if self._record_start_time else 0
            silence_duration = now - self._last_voice_time
            if (elapsed > 2.0 and silence_duration >= SILENCE_AUTOSTOP_SECONDS
                    and not self._silence_stop_fired):
                self._silence_stop_fired = True
                logger.info(f"Silence auto-stop after {silence_duration:.1f}s silence ({elapsed:.1f}s total)")
                threading.Thread(target=self._locked_stop_and_transcribe, daemon=True).start()
                return

            # Max duration guard — MUST run in separate thread to avoid deadlock
            if (self._record_start_time and elapsed > MAX_RECORDING_SECONDS
                    and not self._max_duration_stop_fired):
                self._max_duration_stop_fired = True
                logger.warning(f"Max recording duration ({MAX_RECORDING_SECONDS}s) reached, auto-stopping")
                play_sound("Basso")
                threading.Thread(target=self._locked_stop_and_transcribe, daemon=True).start()

    def _locked_stop_and_transcribe(self):
        """Thread-safe wrapper for auto-stop paths (silence, max duration)."""
        with self._recording_lock:
            self._stop_and_transcribe()

    def _stop_and_transcribe(self):
        if not self.recording:
            return
        self.recording = False
        # HUD stays visible — Phase C keeps it up through transcription + preview.
        # hide() is called inside _on_preview_done (or the early-exit paths below).
        self._processing = True
        self._processing_start = time.time()
        self._last_rcmd_time = 0
        recording_mode = self._recording_mode
        from PyObjCTools import AppHelper
        AppHelper.callAfter(lambda: setattr(self._btn_dictate, 'title', 'Dictate'))

        # HUD → processing state: freeze the timer, swap chip to "PROCESANDO",
        # run the indeterminate waveform sweep. Skipped on the empty-audio
        # early-exit below (HUD just hides).
        if self._hud is not None:
            try:
                self._hud.mark_processing()
            except Exception as exc:
                print(f"[ui] HUD mark_processing failed: {exc}", flush=True)

        # Detach stream reference immediately — audio callback will see recording=False
        stream = self.stream
        self.stream = None

        # Copy frames under lock (fast, no I/O)
        with self._frames_lock:
            frames_copy = list(self.audio_frames)
            self.audio_frames = []

        # Fire-and-forget stream teardown — NEVER blocks the recording lock.
        # Uses internal timeout + PortAudio reset as last resort.
        if stream:
            def _teardown_stream():
                def _do_close():
                    stream.stop()
                    stream.close()
                closer = threading.Thread(target=_do_close, daemon=True)
                closer.start()
                closer.join(timeout=3.0)
                if closer.is_alive():
                    logger.warning("Stream close timed out (3s) — resetting PortAudio in background")
                    self._safe_portaudio_reset("after stream close timeout")
                else:
                    logger.info("Stream closed cleanly")
            threading.Thread(target=_teardown_stream, daemon=True).start()

        if not frames_copy:
            self._processing = False
            self._set_ui(self.ICON_IDLE, "No audio captured")
            if self._hud is not None:
                try:
                    self._hud.hide()
                except Exception as exc:
                    print(f"[ui] HUD hide failed: {exc}", flush=True)
            return

        play_sound(SOUND_STOP_RECORDING)
        engine_label = "groq" if self._mode != MODE_LOCAL else "local"
        action_label = "organizing" if recording_mode == RECORDING_MODE_ORGANIZE else "transcribing"
        self._set_ui(self.ICON_PROCESSING, f"{action_label.capitalize()} ({engine_label})...")
        logger.info(f"Recording stopped. {len(frames_copy)} chunks captured")

        capture_sr = self._capture_sample_rate

        def _hide_hud():
            if self._hud is not None:
                try:
                    self._hud.hide()
                except Exception as exc:
                    print(f"[ui] HUD hide failed: {exc}", flush=True)

        def process():
            _preview_owns_processing = False  # True when preview callback will clear _processing
            try:
                audio = np.concatenate(frames_copy, axis=0).flatten()
                # Resample if device was opened at a non-standard rate (e.g. AirPods at 24kHz)
                if capture_sr != SAMPLE_RATE:
                    audio = resample_audio(audio, capture_sr, SAMPLE_RATE)
                    logger.info(f"Resampled audio {capture_sr} Hz → {SAMPLE_RATE} Hz")
                duration = len(audio) / SAMPLE_RATE
                logger.info(f"Audio duration: {duration:.1f}s")

                rms = np.sqrt(np.mean(audio ** 2))
                peak_chunk_rms = max(
                    float(np.sqrt(np.mean(chunk ** 2))) for chunk in frames_copy
                )
                logger.info(f"Audio RMS: {rms:.6f} (peak chunk RMS: {peak_chunk_rms:.6f})")
                if peak_chunk_rms < SILENCE_RMS_THRESHOLD:
                    logger.info(
                        f"Silent/noise only (peak chunk RMS: {peak_chunk_rms:.6f}), skipping"
                    )
                    play_sound("Funk")
                    self._set_ui(self.ICON_IDLE, "Too quiet — speak closer")
                    _hide_hud()
                    return

                lang_code = LANG_MAP.get(self._language, "es")
                if duration > MAX_CHUNK_SECONDS:
                    text, engine = transcribe_chunked(audio, self._mode, language=lang_code)
                else:
                    text, engine = transcribe_audio(audio, self._mode, language=lang_code)
                if text:
                    if _looks_like_spurious_short_transcript(text, duration, peak_chunk_rms):
                        logger.info(
                            f"Short low-energy artifact-like transcript skipped: {text[:40]}..."
                        )
                        play_sound("Funk")
                        self._set_ui(self.ICON_IDLE, "Nothing heard")
                        _hide_hud()
                        return

                    logger.info(f"[{engine}] Transcribed: {text[:80]}...")
                    raw_text = text
                    engine_label_short = "cloud" if "groq" in engine else "local"
                    target_app_snap = self._target_app

                    # Decide the action for this transcript. Two user-facing modes:
                    # - DICTATE (R⌘⌘):    raw paste, no LLM.
                    # - ROUTE   (Opt+R⌘⌘): smart-dictation — a voice command routes
                    #                      to that action; no command → prompt.
                    # ORGANIZE is a menu-only explicit shortcut.
                    #
                    # Cheap heuristics short-circuit so the common case (a few
                    # words → raw) doesn't pay an LLM round trip. Privacy mode is
                    # respected downstream — a "prompt" classification under
                    # privacy lands on raw paste with a "Pasted raw (privacy)" status.
                    route_decision = None  # set only in RECORDING_MODE_ROUTE
                    if recording_mode == RECORDING_MODE_DICTATE:
                        action_id = "raw"
                    elif recording_mode == RECORDING_MODE_ORGANIZE:
                        action_id = "organize"
                    elif recording_mode == RECORDING_MODE_ROUTE:
                        # Smart-dictation: a canonical voice command (traduce /
                        # responde / organiza / tono) routes to that action. With
                        # NO command the phrase falls through to the prompt
                        # classifier — preserving the legacy OPTIMIZE behavior, so
                        # this hotkey still turns a dictated task into a prompt.
                        # use_llm=False keeps the router itself LLM-free; the
                        # single classification call lives in classify_intent.
                        # Privacy mode skips both → raw.
                        from shared.user_profile import is_privacy_mode
                        if is_privacy_mode():
                            action_id = "raw"
                        else:
                            try:
                                from auto_whisper.text_processor import route_intent
                                route_decision = route_intent(raw_text, use_llm=False)
                                action_id = route_decision.action_id
                                if action_id == "dictate":
                                    # No explicit command → smart-dictation default.
                                    from auto_whisper.text_processor import classify_intent
                                    action_id = classify_intent(raw_text)
                                    logger.info("[router] no command → classify → %s", action_id)
                                else:
                                    logger.info(
                                        "[router] %s → %s (%s, params=%s)",
                                        recording_mode, action_id,
                                        route_decision.source, route_decision.params,
                                    )
                            except Exception as e:
                                logger.warning("Router failed: %s; optimizing as prompt", e)
                                action_id = "prompt_coding"
                    else:
                        # Defensive: unknown mode → raw (legacy OPTIMIZE folded
                        # into ROUTE; classify_intent now lives inside that path).
                        action_id = "raw"

                    def _execute_action(action_id: str, cancelled: bool = False, category: str | None = None):
                        # Runs on main thread (NSTimer callback). Heavy work
                        # is dispatched to a worker thread so we don't block.
                        if cancelled:
                            logger.info("[picker] Cancelled by user")
                            self._set_ui(self.ICON_IDLE, "Cancelled")
                            _hide_hud()
                            self._processing = False
                            return

                        def _worker():
                            output_text = raw_text
                            status_prefix = "Pasted"
                            from shared.user_profile import is_privacy_mode
                            llm_actions = (
                                "organize", "prompt_coding", "prompt_writing",
                                "research", "decision_making",
                                "translate", "tone", "summarize", "reply",
                            )
                            privacy_blocked = is_privacy_mode() and action_id in llm_actions

                            # Router actions may take their payload from the
                            # clipboard when the dictation is instruction-only.
                            router_payload, router_instruction = raw_text, ""
                            if action_id in ("translate", "tone", "reply"):
                                import shared.voice_actions as _va
                                _act = _va.get(action_id)
                                clip = read_clipboard_text() if (_act and _act.needs_payload) else None
                                router_payload, router_instruction = _resolve_payload(
                                    action_id, raw_text, clip
                                )

                            # F5 — make the fallback-to-dictation visible: the
                            # router chose NOT to act, that's not a failure.
                            if recording_mode == RECORDING_MODE_ROUTE and action_id == "raw":
                                if route_decision and route_decision.source == "fallback":
                                    status_prefix = "Dictado (no detecté acción)"
                                else:
                                    status_prefix = "Dictado"

                            if privacy_blocked or (is_privacy_mode() and action_id in llm_actions):
                                # No local LLM yet; surface this clearly instead of
                                # silently degrading to raw without explanation.
                                logger.info(
                                    "Privacy on — %s requires LLM, pasting raw", action_id
                                )
                                status_prefix = "Pasted raw (privacy)"
                            elif action_id == "organize":
                                self._set_ui(self.ICON_PROCESSING, "Organizing ideas...")
                                try:
                                    from auto_whisper.text_processor import organize_ideas
                                    organized = organize_ideas(raw_text)
                                except Exception as e:
                                    logger.error(f"Idea organization failed: {e}")
                                    organized = None
                                if organized and organized.strip():
                                    output_text = organized.strip()
                                    status_prefix = "Organized"
                                else:
                                    logger.warning("Organize returned empty, falling back to raw")
                            elif action_id == "prompt_coding":
                                self._set_ui(self.ICON_PROCESSING, "Optimizing coding prompt...")
                                try:
                                    from auto_whisper.text_processor import optimize_prompt
                                    optimized = optimize_prompt(raw_text, emphasis=category)
                                except Exception as e:
                                    logger.error(f"Prompt optimization failed: {e}")
                                    optimized = None
                                if optimized and optimized.strip():
                                    output_text = optimized.strip()
                                    status_prefix = "Prompt ready (coding)"
                                else:
                                    logger.warning("Optimize returned empty, falling back to raw")
                            elif action_id == "prompt_writing":
                                self._set_ui(self.ICON_PROCESSING, "Optimizing writing...")
                                try:
                                    from auto_whisper.text_processor import optimize_writing
                                    optimized = optimize_writing(raw_text)
                                except Exception as e:
                                    logger.error(f"Writing optimization failed: {e}")
                                    optimized = None
                                if optimized and optimized.strip():
                                    output_text = optimized.strip()
                                    status_prefix = "Writing ready"
                                else:
                                    logger.warning("Writing returned empty, falling back to raw")
                            elif action_id == "research":
                                self._set_ui(self.ICON_PROCESSING, "Building research brief...")
                                try:
                                    from auto_whisper.text_processor import research_brief
                                    optimized = research_brief(raw_text)
                                except Exception as e:
                                    logger.error(f"Research brief failed: {e}")
                                    optimized = None
                                if optimized and optimized.strip():
                                    output_text = optimized.strip()
                                    status_prefix = "Research brief ready"
                                else:
                                    logger.warning("Research returned empty, falling back to raw")
                            elif action_id == "decision_making":
                                self._set_ui(self.ICON_PROCESSING, "Building decision brief...")
                                try:
                                    from auto_whisper.text_processor import decision_brief
                                    optimized = decision_brief(raw_text)
                                except Exception as e:
                                    logger.error(f"Decision brief failed: {e}")
                                    optimized = None
                                if optimized and optimized.strip():
                                    output_text = optimized.strip()
                                    status_prefix = "Decision brief ready"
                                else:
                                    logger.warning("Decision returned empty, falling back to raw")
                            elif action_id == "translate":
                                self._set_ui(self.ICON_PROCESSING, "Traduciendo...")
                                lang = (route_decision.params.get("target_lang")
                                        if route_decision else None) or "English"
                                try:
                                    from auto_whisper.text_processor import translate as _translate
                                    out = _translate(router_payload, target_lang=lang)
                                except Exception as e:
                                    logger.error(f"Translate failed: {e}")
                                    out = None
                                if out and out.strip():
                                    output_text = out.strip()
                                    status_prefix = f"Traducido ({lang})"
                                else:
                                    logger.warning("Translate returned empty, falling back to raw")
                            elif action_id == "tone":
                                tone = (route_decision.params.get("tone")
                                        if route_decision else None) or "formal"
                                self._set_ui(self.ICON_PROCESSING, f"Ajustando tono ({tone})...")
                                try:
                                    from auto_whisper.text_processor import adjust_tone
                                    out = adjust_tone(router_payload, tone=tone)
                                except Exception as e:
                                    logger.error(f"Tone adjust failed: {e}")
                                    out = None
                                if out and out.strip():
                                    output_text = out.strip()
                                    status_prefix = f"Tono ({tone})"
                                else:
                                    logger.warning("Tone returned empty, falling back to raw")
                            elif action_id == "summarize":
                                self._set_ui(self.ICON_PROCESSING, "Resumiendo...")
                                try:
                                    from auto_whisper.text_processor import summarize as _summarize
                                    out = _summarize(raw_text)
                                except Exception as e:
                                    logger.error(f"Summarize failed: {e}")
                                    out = None
                                if out and out.strip():
                                    output_text = out.strip()
                                    status_prefix = "Resumido"
                                else:
                                    logger.warning("Summarize returned empty, falling back to raw")
                            elif action_id == "reply":
                                # F4 — REPLY's payload is the clipboard. Guard the
                                # invisible precondition: if it's empty or doesn't
                                # look like a message (a URL, a path, one token),
                                # don't reply to garbage — paste raw and say why.
                                _pl = router_payload.strip()
                                _looks_like_msg = bool(_pl) and (
                                    " " in _pl and not _pl.startswith(("http://", "https://", "/", "www."))
                                )
                                if not _pl:
                                    logger.info("Reply: empty clipboard, pasting raw")
                                    status_prefix = "Sin mensaje que responder (copia uno primero)"
                                elif not _looks_like_msg:
                                    logger.info("Reply: clipboard not message-like (%r), pasting raw", _pl[:40])
                                    status_prefix = "El portapapeles no parece un mensaje"
                                else:
                                    # Show what it's replying to (F4 transparency).
                                    _preview = _pl[:45] + ("…" if len(_pl) > 45 else "")
                                    self._set_ui(self.ICON_PROCESSING, f"Respondiendo a: «{_preview}»")
                                    try:
                                        from auto_whisper.text_processor import reply_message
                                        out = reply_message(_pl, instruction=router_instruction)
                                    except Exception as e:
                                        logger.error(f"Reply failed: {e}")
                                        out = None
                                    if out and out.strip():
                                        output_text = out.strip()
                                        status_prefix = "Respuesta"
                                    else:
                                        logger.warning("Reply returned empty, falling back to raw")

                            self._last_transcription = output_text
                            self._btn_paste_last.set_callback(self._paste_last)
                            # Enable reformat-last items now that there's a
                            # raw transcript stored in the buffer to operate on.
                            self._btn_reformat_coding.set_callback(self._menu_reformat_coding)
                            self._btn_reformat_writing.set_callback(self._menu_reformat_writing)
                            self._btn_reformat_organize.set_callback(self._menu_reformat_organize)
                            if getattr(self, "_btn_reformat_research", None) is not None:
                                self._btn_reformat_research.set_callback(self._menu_reformat_research)
                            if getattr(self, "_btn_reformat_decision", None) is not None:
                                self._btn_reformat_decision.set_callback(self._menu_reformat_decision)
                            self._btn_reformat_raw.set_callback(self._menu_reformat_raw)

                            # Add to buffer — key by action type for replay.
                            processed_variants: dict[str, str] = {}
                            if output_text != raw_text:
                                buf_key = {
                                    "organize":        "organize",
                                    "prompt_coding":   "optimize",
                                    "prompt_writing":  "writing",
                                    "research":        "research",
                                    "decision_making": "decision",
                                }.get(action_id)
                                if buf_key:
                                    processed_variants[buf_key] = output_text
                            try:
                                _dictation_buffer.add(
                                    raw_text=raw_text,
                                    mode_used=action_id,
                                    language=lang_code,
                                    processed=processed_variants,
                                )
                                from PyObjCTools import AppHelper as _AppHelper
                                _AppHelper.callAfter(self._refresh_recent_menu)
                            except ValueError:
                                logger.debug("Buffer rejected empty raw_text (defensive)")

                            from PyObjCTools import AppHelper as _AppHelper2
                            def _finish():
                                _hide_hud()
                                inject_text(output_text, target_app=target_app_snap, blocking=False)
                                self._set_ui(
                                    self.ICON_IDLE,
                                    f"✓ {status_prefix} ({engine_label_short})",
                                )
                                self._processing = False
                            _AppHelper2.callAfter(_finish)

                        threading.Thread(target=_worker, daemon=True, name="picker-action").start()

                    # Surface the detected intent on the HUD (router mode only)
                    # before pasting — minimal discoverability (Fase 6).
                    if recording_mode == RECORDING_MODE_ROUTE and self._hud is not None:
                        from shared import voice_actions as _va
                        _act = _va.get(action_id)
                        _label = _act.label if _act else "DICTAR"
                        try:
                            self._hud.mark_intent(_label)
                        except Exception as exc:
                            print(f"[ui] HUD intent failed: {exc}", flush=True)

                    # No more picker — execute the resolved action directly.
                    # _execute_action handles its own threading and HUD hide.
                    _execute_action(action_id)
                    _preview_owns_processing = True
                    return

                else:
                    logger.warning("Transcription returned empty")
                    self._set_ui(self.ICON_IDLE, "Nothing heard")
                    _hide_hud()
            finally:
                # Early-exit paths (silence, empty, errors) need this cleared.
                # When preview owns it, _on_preview_done handles the cleanup.
                if not _preview_owns_processing:
                    self._processing = False

        try:
            threading.Thread(target=process, daemon=True).start()
        except Exception as e:
            logger.error(f"Failed to start process thread: {e}")
            self._processing = False
            self._set_ui(self.ICON_IDLE, "Processing failed")
            _hide_hud()

    def _set_ui(self, icon: str, status: str):
        from PyObjCTools import AppHelper
        def _update():
            recording = (icon == self.ICON_RECORDING)
            try:
                self._title_pulse.apply(icon, recording)
            except Exception:
                # Fallback: never let UI breakage block state updates.
                self.title = icon
            try:
                self._status_item.title = status
                self._usage_item.title = usage_tracker.format_bar()
            except Exception:
                pass
        AppHelper.callAfter(_update)

    def _menu_toggle(self, _):
        self._toggle_recording(RECORDING_MODE_DICTATE)

    @rumps.events.before_quit
    def _cleanup(self):
        if self._monitor:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None
        if self.stream:
            try:
                self.stream.stop()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass


def main():
    print(f"\n  auto-whisper {__version__}")
    print("  ─────────────────")

    if not Path(WHISPER_BIN).exists():
        print(f"  ⚠ whisper-cli not found (local mode unavailable)")
    if not GROQ_API_KEY_DICTATION:
        print(f"  ⚠ GROQ_API_KEY_DICTATION not set — add to .env for cloud mode")
        print(f"    Get free key: https://console.groq.com/keys")

    if not GROQ_API_KEY_DICTATION and not Path(WHISPER_BIN).exists():
        print("  ✗ No transcription engine available. Set GROQ_API_KEY_DICTATION or install whisper.cpp.")
        sys.exit(1)

    trusted = check_accessibility()
    if not trusted:
        print("  ⚠ Accessibility permission required!")
        print("  Grant permission, then wait...")
        import ApplicationServices
        for i in range(60):
            time.sleep(1)
            if ApplicationServices.AXIsProcessTrusted():
                trusted = True
                print("  ✓ Permission granted!")
                break
            if i % 5 == 4:
                print(f"  ... waiting ({60 - i}s)")
        if not trusted:
            print("  ✗ Timed out. Grant Accessibility and reopen.\n")
            sys.exit(1)

    default_mode = MODE_CLOUD if GROQ_API_KEY_DICTATION else MODE_LOCAL
    model_name = Path(WHISPER_MODEL).stem.replace("ggml-", "")
    online = is_online() if GROQ_API_KEY_DICTATION else False

    cloud_route = "via auto-whisper-service" if USE_SERVICE_TRANSCRIPTION else "direct Groq API"
    from auto_whisper.processing_routing import USE_SERVICE_PROCESSING
    proc_route = "via auto-whisper-service" if USE_SERVICE_PROCESSING else "direct Groq API"
    from auto_whisper.tts_routing import USE_SERVICE_TTS
    tts_route = "via auto-whisper-service" if USE_SERVICE_TTS else "direct (local synth)"
    from auto_whisper.audio_routing import PREFER_BUILTIN_MIC
    mic_policy = "prefer built-in over Bluetooth" if PREFER_BUILTIN_MIC else "honor system default"

    # If any path is routed through the service, make sure it's running. The
    # auto-spawn here is a beta convenience until the LaunchAgent plist
    # (Phase 5.2) takes over lifecycle management.
    any_service_flag = USE_SERVICE_TRANSCRIPTION or USE_SERVICE_PROCESSING or USE_SERVICE_TTS
    service_status = "not used (all flags off)"
    if any_service_flag:
        from auto_whisper.service_lifecycle import ensure_service_running
        ok = ensure_service_running()
        service_status = "reachable" if ok else "UNREACHABLE — flag-on calls will fail"

    print(f"  ✓ Accessibility: granted")
    print(f"  ✓ Cloud engine: {'Groq whisper-large-v3' if GROQ_API_KEY_DICTATION else 'not configured'} ({cloud_route})")
    print(f"  ✓ LLM processing: {proc_route}")
    print(f"  ✓ TTS: {tts_route}")
    print(f"  ✓ Mic input: {mic_policy}")
    print(f"  ✓ Service: {service_status}")
    print(f"  ✓ Local engine: {model_name if Path(WHISPER_BIN).exists() else 'not available'}")
    print(f"  ✓ Default mode: {default_mode}")
    print(f"  ✓ Internet: {'online' if online else 'offline'}")
    print(f"  ✓ Hotkey: double-tap Right ⌘")
    print(f"  ✓ Switch mode: click ◎ → Engine")
    print()

    logger.info(f"=== auto-whisper {__version__} started ===")
    logger.info(
        f"Mode: {default_mode}, cloud={'groq' if GROQ_API_KEY_DICTATION else 'none'} "
        f"({cloud_route}), processing={proc_route}, tts={tts_route}, local={model_name}"
    )
    app = AutoWhisperApp()
    app.run()


if __name__ == "__main__":
    main()
