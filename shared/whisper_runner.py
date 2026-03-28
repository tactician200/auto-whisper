import subprocess
import tempfile
import logging
from pathlib import Path

from shared.config import WHISPER_BIN, WHISPER_MODEL, WHISPER_MODEL_SMALL, WHISPER_LANGUAGE, SAMPLE_RATE

logger = logging.getLogger(__name__)


def convert_audio(input_path: Path, output_path: Path) -> bool:
    """Convert audio to WAV 16kHz mono PCM using ffmpeg."""
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"ffmpeg failed: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timed out")
        return False


def transcribe_file(wav_path: Path, output_path: Path, model: Path | None = None) -> str | None:
    """Run whisper.cpp on a WAV file. Returns transcript text or None."""
    output_base = str(output_path.with_suffix(""))
    use_model = model or WHISPER_MODEL_SMALL  # small for long files (meetings)
    cmd = [
        str(WHISPER_BIN),
        "-m", str(use_model),
        "-f", str(wav_path),
        "-l", WHISPER_LANGUAGE,
        "-otxt",
        "-of", output_base,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.error(f"whisper failed: {result.stderr}")
            return None

        txt_file = Path(f"{output_base}.txt")
        if txt_file.exists():
            return txt_file.read_text().strip()

        logger.error(f"Transcript file not found: {txt_file}")
        return None
    except subprocess.TimeoutExpired:
        logger.error("whisper timed out")
        return None


