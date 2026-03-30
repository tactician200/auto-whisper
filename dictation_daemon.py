#!/usr/bin/env python3
"""
auto-whisper v4.0 — Live Dictation Daemon

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
import unicodedata
import numpy as np
import sounddevice as sd
import rumps

os.environ.setdefault("LANG", "en_US.UTF-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from pathlib import Path
from AppKit import (
    NSEvent, NSFlagsChangedMask, NSWorkspace,
    NSPasteboard, NSPasteboardTypeString, NSApplication,
)
from Quartz import (
    CGEventCreateKeyboardEvent, CGEventSetFlags, CGEventPost,
    kCGHIDEventTap, kCGEventFlagMaskCommand,
)
from shared.config import (
    WHISPER_BIN, WHISPER_MODEL,
    SAMPLE_RATE, LOGS, GROQ_API_KEY_DICTATION,
)

LOGS.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS / "dictation.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

FRAMES_PER_BUFFER = 1024
RIGHT_CMD_KEYCODE = 54
LEFT_CMD_KEYCODE = 55
DOUBLE_TAP_WINDOW = 0.4
V_KEYCODE = 9
C_KEYCODE = 8

# No sentence prompt — only vocabulary hints to avoid hallucination on long audio
WHISPER_PROMPT = None
MAX_RECORDING_SECONDS = 300  # 5 min auto-stop guard
SILENCE_AUTOSTOP_SECONDS = 5.0  # auto-stop after N seconds of silence
SILENCE_RMS_THRESHOLD = 0.008  # below this RMS = silence
SOUND_START_DICTATE = "Glass"
SOUND_START_ORGANIZE = "Hero"
SOUND_STOP_RECORDING = "Pop"

# Lazy-init Groq client (reuse connection pool)
_groq_client = None
_injection_lock = threading.Lock()


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        _groq_client = Groq(api_key=GROQ_API_KEY_DICTATION)
    return _groq_client

# Transcription modes
MODE_AUTO = "Auto"
MODE_CLOUD = "Cloud (Groq)"
MODE_LOCAL = "Local"

RECORDING_MODE_DICTATE = "dictate"
RECORDING_MODE_ORGANIZE = "organize"

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
        subprocess.Popen(
            ["/usr/bin/afplay", sound_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.warning(f"Failed to play sound '{name}': {e}")


# --- Internet check ---

def is_online() -> bool:
    """Quick check if internet is available (DNS resolve)."""
    import socket
    try:
        socket.create_connection(("api.groq.com", 443), timeout=1.5)
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

def inject_text(text: str, target_app=None):
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
                        if not _wait_for_frontmost_app(target_app, timeout=0.8):
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
                    time.sleep(0.18 + attempt * 0.08)

                    current = NSWorkspace.sharedWorkspace().frontmostApplication()
                    if not target_app or (
                        current and current.processIdentifier() == target_app.processIdentifier()
                    ):
                        paste_sent = True
                        logger.info(f"Paste shortcut sent ({len(text)} chars)")
                        break

                if not paste_sent:
                    logger.warning("Paste shortcut may have missed the target app")
            finally:
                time.sleep(1.2)
                if old_content is not None:
                    board.clearContents()
                    board.setString_forType_(old_content, NSPasteboardTypeString)

    threading.Thread(target=_do_inject, daemon=True).start()


# --- Transcription: Cloud (Groq) ---

def transcribe_cloud(audio_data: np.ndarray, language: str | None = "es") -> str | None:
    """Transcribe via Groq whisper-large-v3 API. ~100ms latency."""
    try:
        pcm = (audio_data * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm.tobytes())
        buf.seek(0)

        client = _get_groq_client()
        t0 = time.time()
        params = dict(
            model="whisper-large-v3",
            file=("audio.wav", buf),
            response_format="text",
        )
        if WHISPER_PROMPT:
            params["prompt"] = WHISPER_PROMPT
        if language:
            params["language"] = language
        result = client.audio.transcriptions.create(**params)
        elapsed = time.time() - t0
        logger.info(f"Groq transcription: {elapsed:.1f}s")

        if not isinstance(result, str):
            logger.error(f"Unexpected Groq response type: {type(result)}")
            return None
        text = result.strip()
        if text:
            return _clean_transcription(text) or None
        return None

    except Exception as e:
        logger.error(f"Groq API failed: {e}")
        return None


# --- Transcription: Local (whisper.cpp) ---

def transcribe_local(audio_data: np.ndarray, language: str | None = "es") -> str | None:
    """Transcribe via local whisper.cpp. ~6-8s latency."""
    tmpdir = tempfile.mkdtemp()
    try:
        wav_path = os.path.join(tmpdir, "audio.wav")
        out_base = os.path.join(tmpdir, "out")

        with wave.open(wav_path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            pcm = (audio_data * 32767).astype(np.int16)
            wf.writeframes(pcm.tobytes())

        cmd = [
            str(WHISPER_BIN), "-m", str(WHISPER_MODEL),
            "-f", wav_path,
            "-otxt", "-of", out_base, "--no-timestamps",
            "--beam-size", "8",
            "--entropy-thold", "2.4",
        ]
        if WHISPER_PROMPT:
            cmd.extend(["--prompt", WHISPER_PROMPT])
        if language:
            cmd.extend(["-l", language])

        t0 = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", timeout=120)
        elapsed = time.time() - t0
        logger.info(f"Local transcription: {elapsed:.1f}s")

        txt_file = Path(f"{out_base}.txt")
        if result.returncode == 0 and txt_file.exists():
            text = txt_file.read_text(encoding="utf-8").strip()
            WHISPER_ARTIFACTS = {"[BLANK_AUDIO]", "[Music]", "[Applause]", "[Silence]", "(Silence)", "(Music)"}
            if text and text not in WHISPER_ARTIFACTS:
                return _clean_transcription(text) or None
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

    def __init__(self):
        self._lock = threading.Lock()
        self._date = None
        self.audio_seconds = 0.0
        self.requests = 0
        self._reset_if_new_day()

    def _reset_if_new_day(self):
        from datetime import date
        today = date.today()
        if self._date != today:
            self._date = today
            self.audio_seconds = 0.0
            self.requests = 0

    def record(self, audio_duration: float):
        with self._lock:
            self._reset_if_new_day()
            self.audio_seconds += audio_duration
            self.requests += 1

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
            total_min = self.DAILY_AUDIO_LIMIT / 60
        blocks = int(pct / 10)
        bar = "▓" * blocks + "░" * (10 - blocks)
        return f"Usage: {bar} {used_min:.0f}/{total_min:.0f}min ({pct:.0f}%)"

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


def _clean_transcription(text: str) -> str:
    text = text.strip(" \n\t-–—")
    while "  " in text:
        text = text.replace("  ", " ")

    normalized = _normalize_text(text)
    if not normalized:
        return ""

    exact_artifacts = {
        "subtitulos realizados por la comunidad de amara org",
        "subtitulos por la comunidad de amara org",
        "gracias por ver el video",
        "suscribete al canal",
        "hola buenos dias",
    }
    if normalized in exact_artifacts:
        return ""
    return text


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return " ".join(text.split())


def _looks_like_spurious_short_transcript(text: str, duration: float, rms: float) -> bool:
    normalized = _normalize_text(text)
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

    def __init__(self):
        super().__init__("auto-whisper", quit_button="Quit")
        # Force accessory/menu-bar behavior even when launched directly via python.
        NSApplication.sharedApplication().setActivationPolicy_(1)
        self.recording = False
        self.audio_frames = []
        self._frames_lock = threading.Lock()
        self.stream = None
        self._target_app = None
        self._monitor = None
        self._record_start_time = None
        self.title = self.ICON_IDLE
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

        # Default mode
        self._mode = MODE_CLOUD if GROQ_API_KEY_DICTATION else MODE_LOCAL
        self._language = LANG_ES

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

        self._usage_item = rumps.MenuItem(usage_tracker.format_bar())
        self._status_item = rumps.MenuItem("Status: idle")

        # Build menu items with callbacks assigned explicitly
        self._btn_dictate = rumps.MenuItem("Dictate (Right ⌘⌘)")
        self._btn_dictate.set_callback(self._menu_toggle)
        self._btn_organize = rumps.MenuItem("Organize ideas")
        self._btn_organize.set_callback(self._menu_organize)
        self._btn_summarize = rumps.MenuItem("Summarize clipboard")
        self._btn_summarize.set_callback(self._menu_summarize)
        self._btn_read = rumps.MenuItem("Read clipboard")
        self._btn_read.set_callback(self._menu_read)
        self._btn_explain = rumps.MenuItem("Explain clipboard")
        self._btn_explain.set_callback(self._menu_explain)
        self._btn_paste = rumps.MenuItem("Paste last")
        self._btn_paste.set_callback(self._paste_last)

        self.menu = [
            self._btn_dictate,
            self._btn_organize,
            None,
            self._btn_summarize,
            self._btn_read,
            self._btn_explain,
            None,
            self._btn_paste,
            [rumps.MenuItem("Engine"), [self._mode_cloud, self._mode_local, self._mode_auto]],
            [rumps.MenuItem("Language"), [self._lang_es, self._lang_en, self._lang_auto]],
            self._usage_item,
            self._status_item,
        ]
        self._setup_hotkey()

    def _set_mode(self, sender):
        self._mode = sender.title
        self._update_mode_checks()
        logger.info(f"Mode changed to: {self._mode}")

    def _update_mode_checks(self):
        self._mode_cloud.state = self._mode == MODE_CLOUD
        self._mode_local.state = self._mode == MODE_LOCAL
        self._mode_auto.state = self._mode == MODE_AUTO

    def _set_language(self, sender):
        self._language = sender.title
        self._update_lang_checks()
        logger.info(f"Language changed to: {self._language}")

    def _update_lang_checks(self):
        self._lang_auto.state = self._language == LANG_AUTO
        self._lang_es.state = self._language == LANG_ES
        self._lang_en.state = self._language == LANG_EN

    def _paste_last(self, _):
        """Re-paste the last transcription."""
        if self._last_transcription:
            inject_text(self._last_transcription, target_app=get_frontmost_app())
        else:
            self._set_ui(self.ICON_IDLE, "No previous transcription")

    # --- Text processing actions ---

    _processing_lock = threading.Lock()

    def _process_selection(self, action: str, use_hotkey: bool = False):
        """
        Process text: summarize/read/explain.
        use_hotkey=True: simulate Cmd+C to capture selection (from hotkey)
        use_hotkey=False: read clipboard as-is (from menu, focus already lost)
        """
        if not self._processing_lock.acquire(blocking=False):
            # If speaking, stop it and release lock
            from voice_agent import is_speaking, stop_speaking
            if is_speaking():
                stop_speaking()
            logger.info(f"Already processing, ignoring {action}")
            return

        def _do():
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
                logger.info(f"[{action}] Processing {len(text)} chars...")

                # 2. Process based on action
                if action == "read":
                    voice_text = text
                else:
                    self._set_ui(self.ICON_PROCESSING, f"{action.capitalize()}...")
                    play_sound("Glass")
                    from text_processor import summarize, explain
                    voice_text = summarize(text) if action == "summarize" else explain(text)

                if not voice_text:
                    self._set_ui(self.ICON_IDLE, "Processing failed")
                    return

                # 3. Speak (release lock first so stop works)
                self._set_ui(self.ICON_SPEAKING, f"Speaking...")
            finally:
                self._processing_lock.release()

            # Speaking happens outside the lock — can be stopped anytime
            try:
                from voice_agent import speak
                speak(voice_text)
            finally:
                self._set_ui(self.ICON_IDLE, "Done")

        threading.Thread(target=_do, daemon=True).start()

    def _menu_summarize(self, _):
        logger.info("Menu: Summarize clipboard")
        self._process_selection("summarize", use_hotkey=False)

    def _menu_organize(self, _):
        logger.info("Menu: Organize ideas")
        self._toggle_recording(RECORDING_MODE_ORGANIZE)

    def _menu_read(self, _):
        logger.info("Menu: Read clipboard")
        self._process_selection("read", use_hotkey=False)

    def _menu_explain(self, _):
        logger.info("Menu: Explain clipboard")
        self._process_selection("explain", use_hotkey=False)

    # --- Hotkeys ---

    def _setup_hotkey(self):
        def handler(event):
            try:
                kc = event.keyCode()
                flags = event.modifierFlags()

                # Right ⌘ — dictation toggle
                if kc == RIGHT_CMD_KEYCODE:
                    rcmd_down = bool(flags & (1 << 4))
                    if rcmd_down and not self._rcmd_was_down:
                        now = time.time()
                        if now - self._last_rcmd_time < DOUBLE_TAP_WINDOW:
                            logger.info("Double-tap Right ⌘ → dictation")
                            self._toggle_recording()
                            self._last_rcmd_time = 0
                        else:
                            self._last_rcmd_time = now
                    self._rcmd_was_down = rcmd_down

                # Left ⌘ — summarize selection (or stop if speaking)
                elif kc == LEFT_CMD_KEYCODE:
                    lcmd_down = bool(flags & kCGEventFlagMaskCommand)
                    if lcmd_down and not self._lcmd_was_down:
                        now = time.time()
                        if now - self._last_lcmd_time < DOUBLE_TAP_WINDOW:
                            from voice_agent import is_speaking, stop_speaking
                            if is_speaking():
                                logger.info("Double-tap Left ⌘ → stop speaking")
                                stop_speaking()
                                self._set_ui(self.ICON_IDLE, "Stopped")
                            else:
                                logger.info("Double-tap Left ⌘ → summarize")
                                play_sound("Tink")
                                self._process_selection("summarize", use_hotkey=True)
                            self._last_lcmd_time = 0
                        else:
                            self._last_lcmd_time = now
                    self._lcmd_was_down = lcmd_down

            except Exception as e:
                logger.error(f"Hotkey handler error: {e}")

        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSFlagsChangedMask, handler
        )
        logger.info("Hotkeys: Right ⌘⌘ = dictate, Left ⌘⌘ = summarize")

    def _toggle_recording(self, recording_mode: str = RECORDING_MODE_DICTATE):
        if self.recording:
            self._stop_and_transcribe()
        else:
            self._start_recording(recording_mode)

    def _start_recording(self, recording_mode: str = RECORDING_MODE_DICTATE):
        if self.recording:
            return
        self._recording_mode = recording_mode
        with self._frames_lock:
            self.audio_frames = []
        now = time.time()
        self._record_start_time = None
        self._last_voice_time = now
        self._silence_stop_fired = False
        self._max_duration_stop_fired = False
        self.recording = True
        self._target_app = get_frontmost_app()
        start_label = "Starting mic..." if recording_mode == RECORDING_MODE_DICTATE else "Starting organizer..."
        self._set_ui(self.ICON_STARTING, start_label)
        target_name = self._target_app.localizedName() if self._target_app else "unknown"
        start_sound = SOUND_START_ORGANIZE if recording_mode == RECORDING_MODE_ORGANIZE else SOUND_START_DICTATE
        # Play the cue immediately so the user gets confirmation even if the
        # input stream takes a moment to initialize.
        play_sound(start_sound)
        logger.info(
            f"Initializing mic... (target: {target_name}, mode: {self._mode}, action: {recording_mode})"
        )

        def record():
            try:
                self.stream = sd.InputStream(
                    samplerate=SAMPLE_RATE, channels=1,
                    dtype="float32", blocksize=FRAMES_PER_BUFFER,
                    callback=self._audio_callback,
                )
                started_at = time.time()
                self._record_start_time = started_at
                self._last_voice_time = started_at
                self.stream.start()
                status = "Recording..." if recording_mode == RECORDING_MODE_DICTATE else "Recording ideas..."
                self._set_ui(self.ICON_RECORDING, status)
                logger.info(f"Recording started ({recording_mode})")
            except Exception as e:
                logger.error(f"Failed to start recording: {e}")
                self.recording = False
                self._set_ui(self.ICON_IDLE, "Mic error")

        threading.Thread(target=record, daemon=True).start()

    def _audio_callback(self, indata, frames, time_info, status):
        if self.recording:
            with self._frames_lock:
                self.audio_frames.append(indata.copy())

            now = time.time()
            rms = float(np.sqrt(np.mean(indata ** 2)))

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
                threading.Thread(target=self._stop_and_transcribe, daemon=True).start()
                return

            # Max duration guard — MUST run in separate thread to avoid deadlock
            if (self._record_start_time and elapsed > MAX_RECORDING_SECONDS
                    and not self._max_duration_stop_fired):
                self._max_duration_stop_fired = True
                logger.warning(f"Max recording duration ({MAX_RECORDING_SECONDS}s) reached, auto-stopping")
                play_sound("Basso")
                threading.Thread(target=self._stop_and_transcribe, daemon=True).start()

    def _stop_and_transcribe(self):
        self.recording = False
        recording_mode = self._recording_mode

        # Stop stream with timeout to prevent main-thread freeze
        stream = self.stream
        self.stream = None
        if stream:
            def _close_stream():
                try:
                    stream.stop()
                    stream.close()
                except Exception as e:
                    logger.warning(f"Stream close error: {e}")
            closer = threading.Thread(target=_close_stream, daemon=True)
            closer.start()
            closer.join(timeout=3.0)
            if closer.is_alive():
                logger.warning("Stream close timed out (3s), continuing anyway")

        with self._frames_lock:
            frames_copy = list(self.audio_frames)
            self.audio_frames = []

        if not frames_copy:
            self._set_ui(self.ICON_IDLE, "No audio captured")
            return

        play_sound(SOUND_STOP_RECORDING)
        engine_label = "groq" if self._mode != MODE_LOCAL else "local"
        action_label = "organizing" if recording_mode == RECORDING_MODE_ORGANIZE else "transcribing"
        self._set_ui(self.ICON_PROCESSING, f"{action_label.capitalize()} ({engine_label})...")
        logger.info(f"Recording stopped. {len(frames_copy)} chunks captured")

        def process():
            audio = np.concatenate(frames_copy, axis=0).flatten()
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
                play_sound("Funk")  # notify user it was too quiet
                self._set_ui(self.ICON_IDLE, f"Too quiet (peak RMS: {peak_chunk_rms:.4f})")
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
                    self._set_ui(self.ICON_IDLE, "No speech detected")
                    return

                logger.info(f"[{engine}] Transcribed: {text[:80]}...")
                output_text = text

                if recording_mode == RECORDING_MODE_ORGANIZE:
                    self._set_ui(self.ICON_PROCESSING, "Organizing ideas...")
                    try:
                        from text_processor import organize_ideas
                        organized = organize_ideas(text)
                    except Exception as e:
                        logger.error(f"Idea organization failed: {e}")
                        organized = None

                    if organized and organized.strip():
                        output_text = organized.strip()
                        logger.info(f"Ideas organized: {output_text[:80]}...")
                    else:
                        logger.warning("Idea organization returned empty, falling back to raw transcript")

                self._last_transcription = output_text
                inject_text(output_text, target_app=self._target_app)
                status_prefix = "Ideas organized" if recording_mode == RECORDING_MODE_ORGANIZE else "OK"
                self._set_ui(self.ICON_IDLE, f"{status_prefix} ({engine}): {output_text[:30]}...")
            else:
                logger.warning("Transcription returned empty")
                self._set_ui(self.ICON_IDLE, "No speech detected")

        threading.Thread(target=process, daemon=True).start()

    def _set_ui(self, icon: str, status: str):
        from PyObjCTools import AppHelper
        def _update():
            self.title = icon
            try:
                self._status_item.title = f"Status: {status}"
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
                self.stream.close()
            except Exception:
                pass


def main():
    print("\n  auto-whisper v4.0")
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

    print(f"  ✓ Accessibility: granted")
    print(f"  ✓ Cloud engine: {'Groq whisper-large-v3' if GROQ_API_KEY_DICTATION else 'not configured'}")
    print(f"  ✓ Local engine: {model_name if Path(WHISPER_BIN).exists() else 'not available'}")
    print(f"  ✓ Default mode: {default_mode}")
    print(f"  ✓ Internet: {'online' if online else 'offline'}")
    print(f"  ✓ Hotkey: double-tap Right ⌘")
    print(f"  ✓ Switch mode: click ◎ → Engine")
    print()

    logger.info(f"=== auto-whisper v4.0 started ===")
    logger.info(f"Mode: {default_mode}, cloud={'groq' if GROQ_API_KEY_DICTATION else 'none'}, local={model_name}")
    app = AutoWhisperApp()
    app.run()


if __name__ == "__main__":
    main()
