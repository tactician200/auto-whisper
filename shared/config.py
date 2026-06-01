import os
from pathlib import Path

# Load .env file
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            val = val.strip().strip("'\"")  # strip quotes
            val = val.split("#")[0].strip()  # strip inline comments
            os.environ.setdefault(key.strip(), val)

# Directories
HOME = Path.home()
INBOX = HOME / "MeetingInbox"
DONE = HOME / "MeetingDone"
TRANSCRIPTS = HOME / "MeetingTranscripts"
LOGS = TRANSCRIPTS / "logs"  # meetings-intel logs
AUTO_WHISPER_LOGS = HOME / "Library" / "Logs" / "auto-whisper"  # dictation logs

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

# Groq (cloud transcription) — dual accounts for free tier maximization
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")  # legacy fallback
GROQ_API_KEY_DICTATION = os.environ.get("GROQ_API_KEY_DICTATION", GROQ_API_KEY)
GROQ_API_KEY_MEETINGS = os.environ.get("GROQ_API_KEY_MEETINGS", GROQ_API_KEY)

# Anthropic (Claude) — reasoning-heavy voice actions in the intent router
# (tone, translate, reply). Simple transforms stay on Groq.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_KEY_DICTATION = os.environ.get("ANTHROPIC_API_KEY_DICTATION", ANTHROPIC_API_KEY)
CLAUDE_MODEL = "claude-haiku-4-5"  # fast + cheap; ample for short text transforms

# Audio
SUPPORTED_FORMATS = {".m4a", ".mp3", ".wav", ".aac", ".webm", ".ogg", ".mp4", ".opus"}
SAMPLE_RATE = 16000

# Prompts
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Meeting contexts (future: per-company knowledge)
CONTEXTS_DIR = TRANSCRIPTS / "contexts"
