import shutil
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


def transcribe_wav(
    wav_path: Path,
    model: Path | None = None,
    language: str | None = None,
    beam_size: int | None = None,
    entropy_thold: float | None = None,
    no_timestamps: bool = False,
    prompt: str | None = None,
    timeout: int = 600,
) -> str | None:
    """Run whisper.cpp on a WAV file. Returns transcript text or None.

    Args:
        model: whisper model path (default: WHISPER_MODEL for dictation quality)
        language: language code (default: from config)
        beam_size: beam search width (higher = better quality, slower)
        entropy_thold: entropy threshold for hallucination filtering
        no_timestamps: skip timestamp output
        prompt: initial prompt / vocabulary hints
        timeout: subprocess timeout in seconds
    """
    output_dir = tempfile.mkdtemp()
    output_base = str(Path(output_dir) / "out")
    use_model = model or WHISPER_MODEL
    use_language = language or WHISPER_LANGUAGE

    cmd = [
        str(WHISPER_BIN),
        "-m", str(use_model),
        "-f", str(wav_path),
        "-l", use_language,
        "-otxt",
        "-of", output_base,
    ]
    if no_timestamps:
        cmd.append("--no-timestamps")
    if beam_size is not None:
        cmd.extend(["--beam-size", str(beam_size)])
    if entropy_thold is not None:
        cmd.extend(["--entropy-thold", str(entropy_thold)])
    if prompt:
        cmd.extend(["--prompt", prompt])

    proc = None
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        if proc.returncode != 0:
            logger.error(f"whisper failed: {stderr}")
            return None

        txt_file = Path(f"{output_base}.txt")
        if txt_file.exists():
            return txt_file.read_text(encoding="utf-8").strip()

        logger.error(f"Transcript file not found: {txt_file}")
        return None
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
            proc.wait()
        logger.error("whisper timed out (killed)")
        return None
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def transcribe_file(wav_path: Path, output_path: Path, model: Path | None = None) -> str | None:
    """Run whisper.cpp for meetings (uses small model, writes to output_path)."""
    use_model = model or WHISPER_MODEL_SMALL
    output_base = str(output_path.with_suffix(""))
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


