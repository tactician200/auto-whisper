"""auto-whisper-service — local HTTP service for transcription, processing, and TTS.

Runs as a daemon (LaunchAgent in production, foreground in development).
Exposes a token-authenticated HTTP API on localhost. Designed to be
language-agnostic so future clients (iPad/iPhone) can talk the same protocol.

Architecture: see plans/design-v5.md Section 1-3 of Arch-2.
Phase 1 scope: skeleton only — /health, /version. No transcription yet.
"""

__version__ = "0.1.0"
SCHEMA_VERSION = 1
SERVICE_NAME = "auto-whisper-service"
