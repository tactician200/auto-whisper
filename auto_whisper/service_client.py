"""HTTP client stub for talking to auto-whisper-service.

Phase 1 scope: minimal — health probe + version check. Used by the
menubar app to decide if the service is reachable and trustworthy
before routing real work through it.

Phase 2 will add transcribe / process / explain endpoints. Keeping this
client thin and synchronous (httpx.Client, not AsyncClient) — the
menubar runs on a single rumps thread and async would just add
complexity without latency benefit at the IPC layer (~1-2ms).

NOT imported by dictation_daemon.py yet. The feature flag that toggles
"use service for transcription" lands in Phase 2 alongside the actual
/transcribe endpoint.
"""

import logging

import httpx

from auto_whisper_service.auth import AUTH_HEADER, get_or_create_token
from auto_whisper_service.config import SERVICE_HOST, SERVICE_PORT

logger = logging.getLogger(__name__)


class ServiceClient:
    """Thin synchronous client. Reads the token off disk on construction.

    Per design D1: privacy-mode-friendly because the service runs locally;
    no network egress for client-service IPC.
    """

    def __init__(
        self,
        host: str = SERVICE_HOST,
        port: int = SERVICE_PORT,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={AUTH_HEADER: get_or_create_token()},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ServiceClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── Probes ────────────────────────────────────────────────────────

    def health(self) -> bool:
        """True if the service is reachable and healthy.

        Health endpoint is unauthenticated, so a 200 from any reachable
        process at the configured port returns True. Use `version()` to
        confirm it's actually our service with our token.
        """
        try:
            resp = self._client.get("/health")
            return resp.status_code == 200 and resp.json().get("status") == "ok"
        except httpx.HTTPError as e:
            logger.debug(f"service health probe failed: {e}")
            return False

    def version(self) -> dict | None:
        """Return service metadata if auth succeeds, else None.

        A None result means: service is reachable but our token is wrong
        (token rotated, file deleted, or talking to a different service
        listening on the same port). Caller should regenerate or fail loud.
        """
        try:
            resp = self._client.get("/version")
            if resp.status_code != 200:
                logger.warning(f"version probe got {resp.status_code}: {resp.text[:200]}")
                return None
            return resp.json()
        except httpx.HTTPError as e:
            logger.debug(f"service version probe failed: {e}")
            return None

    # ── Transcription ─────────────────────────────────────────────────

    def transcribe(
        self,
        audio_bytes: bytes,
        language: str | None = "es",
        project: str | None = None,
        filename: str = "audio.wav",
        timeout: float = 30.0,
    ) -> dict | None:
        """Send WAV audio to /transcribe, return parsed response or None.

        Args:
            audio_bytes: WAV-encoded mono 16-bit PCM bytes.
            language: ISO 639-1 hint passed to Groq (None lets the model auto-detect).
            project: optional project tag for vocabulary scoping. Service uses it
                to pick the right vocab subset for hint generation + correction.
            filename: only used in the multipart filename field; service ignores
                content beyond extension matching.
            timeout: per-request timeout (default 30s — Groq normally ~1s but
                allow headroom for 5-min audio uploads).

        Returns:
            dict with keys {text, language, duration_s, cleaned} on 200.
            None on any failure (network error, auth failure, upstream 5xx,
            invalid input rejected by service). Errors are logged at WARNING
            so caller can decide retry/fallback strategy without parsing
            response codes.
        """
        # Build form data — only include fields explicitly set so the service
        # gets clean defaults rather than empty-string values.
        data: dict = {}
        if language:
            data["language"] = language
        if project:
            data["project"] = project

        try:
            resp = self._client.post(
                "/transcribe",
                files={"audio": (filename, audio_bytes, "audio/wav")},
                data=data or None,
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            logger.warning(f"transcribe request failed: {e}")
            return None

        if resp.status_code != 200:
            logger.warning(
                f"transcribe got {resp.status_code}: {resp.text[:200]}"
            )
            return None

        try:
            return resp.json()
        except ValueError as e:
            logger.warning(f"transcribe response not JSON: {e}")
            return None

    # ── TTS ───────────────────────────────────────────────────────────

    def tts(
        self,
        text: str,
        backend: str | None = None,
        voice: str | None = None,
        timeout: float = 30.0,
    ) -> tuple[bytes, str] | None:
        """Send text to /tts, return (audio_bytes, format_ext) or None on failure.

        format_ext is "mp3" (google/edge) or "aiff" (macos) — caller writes the
        bytes to a temp file with that extension and plays via afplay.

        backend: None lets the service pick its DEFAULT_BACKEND (currently
        "edge"). voice: None means backend default voice.

        Returns None on any failure (network, auth, 4xx/5xx, missing format
        header). Errors are logged at WARNING; caller decides retry/fallback.
        """
        body: dict = {"text": text}
        if backend:
            body["backend"] = backend
        if voice:
            body["voice"] = voice

        try:
            resp = self._client.post("/tts", json=body, timeout=timeout)
        except httpx.HTTPError as e:
            logger.warning(f"tts request failed: {e}")
            return None

        if resp.status_code != 200:
            logger.warning(f"tts got {resp.status_code}: {resp.text[:200]}")
            return None

        ext = resp.headers.get("X-TTS-Format")
        if not ext:
            logger.warning("tts response missing X-TTS-Format header")
            return None

        return resp.content, ext

    # ── LLM processing ────────────────────────────────────────────────

    def process(
        self,
        mode: str,
        text: str,
        timeout: float = 30.0,
    ) -> dict | None:
        """Send text to /process for LLM-based transformation.

        mode: one of summarize | explain | explain_paste | organize_ideas |
              optimize_prompt (must match shared.processing.MODES).

        Returns dict {result, mode, duration_s} on 200. None on any failure
        (network, auth, 4xx/5xx, non-JSON body). result may itself be None
        when the underlying LLM call returned None — that's NOT a failure
        of /process; the daemon should treat it the same way it would treat
        a None from shared.processing directly (skip paste, log a warning).
        """
        try:
            resp = self._client.post(
                "/process",
                json={"mode": mode, "text": text},
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            logger.warning(f"process({mode}) request failed: {e}")
            return None

        if resp.status_code != 200:
            logger.warning(f"process({mode}) got {resp.status_code}: {resp.text[:200]}")
            return None

        try:
            return resp.json()
        except ValueError as e:
            logger.warning(f"process({mode}) response not JSON: {e}")
            return None
