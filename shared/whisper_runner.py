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


def transcribe_chunk(audio_data, sample_rate: int = SAMPLE_RATE) -> str | None:
    """Transcribe a numpy audio array (for live dictation). Returns text or None."""
    import numpy as np

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        import wave
        with wave.open(str(tmp_path), "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes((audio_data * 32767).astype(np.int16).tobytes())

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as out_tmp:
            out_path = Path(out_tmp.name)

        text = transcribe_file(tmp_path, out_path)
        return text if text else None
    finally:
        tmp_path.unlink(missing_ok=True)
        Path(str(out_path.with_suffix("")) + ".txt").unlink(missing_ok=True)
