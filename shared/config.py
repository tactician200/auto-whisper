import os
from pathlib import Path

# Load .env file
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

# Directories
HOME = Path.home()
INBOX = HOME / "MeetingInbox"
DONE = HOME / "MeetingDone"
TRANSCRIPTS = HOME / "MeetingTranscripts"
LOGS = TRANSCRIPTS / "logs"

# Output notes folder (open as Obsidian vault if desired)
NOTES_DIR = HOME / "MeetingTranscripts" / "notes"

# Whisper
WHISPER_DIR = HOME / "src" / "whisper.cpp"
WHISPER_BIN = WHISPER_DIR / "build" / "bin" / "whisper-cli"
WHISPER_MODEL = WHISPER_DIR / "models" / "ggml-medium.bin"
WHISPER_MODEL_SMALL = WHISPER_DIR / "models" / "ggml-small.bin"  # faster, for meetings
WHISPER_LANGUAGE = "es"

# Gemini
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"

# Groq (cloud transcription)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# Audio
SUPPORTED_FORMATS = {".m4a", ".mp3", ".wav", ".aac", ".webm", ".ogg", ".mp4"}
SAMPLE_RATE = 16000

# Dictation
DICTATION_HOTKEY = "<cmd>+<shift>+<space>"
DICTATION_CHUNK_SECONDS = 3
DICTATION_IDLE_TIMEOUT = 60  # seconds before unloading model

# Prompts
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
