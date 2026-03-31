#!/usr/bin/env python3
"""
Meeting Transcription Pipeline v2
Watches ~/MeetingInbox/ for audio files, transcribes with Groq API (or local fallback),
analyzes with Gemini API, and generates Obsidian markdown notes.

Supports progressive processing: long meetings generate partial notes as they're transcribed.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from shared.config import (
    INBOX, DONE, LOGS, NOTES_DIR, CONTEXTS_DIR,
    SUPPORTED_FORMATS, GEMINI_API_KEY, GEMINI_MODEL, PROMPTS_DIR,
    GROQ_API_KEY_MEETINGS, WHISPER_LANGUAGE,
)

os.environ.setdefault("LANG", "en_US.UTF-8")

LOGS.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS / "transcriber.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Groq limits
MAX_DIRECT_UPLOAD_MB = 25
CHUNK_MINUTES = 20  # split long audio into segments


def is_file_stable(path: Path, wait_seconds: int = 3) -> bool:
    """Check if file is done being written (size stable)."""
    size1 = path.stat().st_size
    time.sleep(wait_seconds)
    size2 = path.stat().st_size
    return size1 == size2 and size1 > 0


# --- Audio utilities ---

def get_audio_duration(path: Path) -> float:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0


def compress_audio(input_path: Path, output_path: Path) -> bool:
    """Compress audio to MP3 32kbps mono (small enough for Groq, good enough for speech)."""
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-ac", "1", "-ar", "16000", "-b:a", "32k",
        "-f", "mp3", str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Compression failed: {e}")
        return False


def split_audio(input_path: Path, chunk_minutes: int, tmpdir: Path) -> list[Path]:
    """Split audio into chunks of N minutes using ffmpeg."""
    duration = get_audio_duration(input_path)
    if duration <= 0:
        return [input_path]

    chunk_seconds = chunk_minutes * 60
    chunks = []
    start = 0
    i = 0

    while start < duration:
        chunk_path = tmpdir / f"chunk_{i:03d}.mp3"
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-ss", str(start), "-t", str(chunk_seconds),
            "-ac", "1", "-ar", "16000", "-b:a", "32k",
            "-f", "mp3", str(chunk_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and chunk_path.exists() and chunk_path.stat().st_size > 0:
            chunks.append(chunk_path)
        start += chunk_seconds
        i += 1

    return chunks if chunks else [input_path]


# --- Transcription ---

def transcribe_with_groq(audio_path: Path) -> str | None:
    """Transcribe via Groq whisper-large-v3 API."""
    if not GROQ_API_KEY_MEETINGS:
        return None

    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY_MEETINGS)

        file_size_mb = audio_path.stat().st_size / (1024 * 1024)

        # Compress if too large for direct upload
        send_path = audio_path
        compressed_path = None
        if file_size_mb > MAX_DIRECT_UPLOAD_MB:
            logger.info(f"File too large ({file_size_mb:.0f}MB), compressing...")
            compressed_path = audio_path.parent / f"{audio_path.stem}_compressed.mp3"
            if compress_audio(audio_path, compressed_path):
                send_path = compressed_path
                logger.info(f"Compressed to {compressed_path.stat().st_size / (1024*1024):.1f}MB")
            else:
                logger.warning("Compression failed, trying direct upload")

        t0 = time.time()
        with open(send_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=(send_path.name, f),
                language=WHISPER_LANGUAGE,
                response_format="verbose_json",
            )
        elapsed = time.time() - t0
        logger.info(f"Groq transcription: {elapsed:.1f}s")

        # Clean up compressed file
        if compressed_path and compressed_path.exists():
            compressed_path.unlink()

        # Extract text from verbose_json
        if hasattr(result, 'text'):
            return result.text.strip()
        elif isinstance(result, dict) and 'text' in result:
            return result['text'].strip()
        return str(result).strip()

    except Exception as e:
        logger.error(f"Groq transcription failed: {e}")
        return None


def transcribe_with_local(audio_path: Path) -> str | None:
    """Fallback: transcribe with local whisper.cpp."""
    from shared.whisper_runner import convert_audio, transcribe_file

    with tempfile.TemporaryDirectory(prefix="whisper_") as tmpdir:
        tmpdir = Path(tmpdir)
        wav_path = tmpdir / "audio.wav"

        if not convert_audio(audio_path, wav_path):
            return None

        transcript_path = tmpdir / "transcript"
        return transcribe_file(wav_path, transcript_path)


def transcribe_meeting(audio_path: Path, tmpdir: Path) -> str | None:
    """
    Smart transcription: Groq cloud with chunking for long files, local fallback.
    """
    duration = get_audio_duration(audio_path)
    duration_min = duration / 60
    logger.info(f"Audio duration: {duration_min:.1f} min")

    # Short meeting: single Groq call
    if duration_min <= CHUNK_MINUTES and GROQ_API_KEY_MEETINGS:
        text = transcribe_with_groq(audio_path)
        if text:
            logger.info(f"[groq] Transcribed {len(text)} chars")
            return text
        logger.warning("Groq failed, trying local...")

    # Long meeting: chunk and transcribe progressively
    if duration_min > CHUNK_MINUTES and GROQ_API_KEY_MEETINGS:
        logger.info(f"Long meeting ({duration_min:.0f} min), splitting into {CHUNK_MINUTES}-min chunks")
        chunks = split_audio(audio_path, CHUNK_MINUTES, tmpdir)
        segments = []

        for i, chunk_path in enumerate(chunks):
            logger.info(f"Transcribing chunk {i+1}/{len(chunks)}...")
            text = transcribe_with_groq(chunk_path)
            if text:
                segments.append(text.strip())
                logger.info(f"  Chunk {i+1}: {len(text)} chars")

                # Save partial note
                partial_text = " ".join(segments)
                save_note(audio_path, partial_text, None,
                         partial=f"{i+1}/{len(chunks)}")

        if segments:
            full_text = " ".join(segments)
            logger.info(f"[groq-chunked] Transcribed {len(full_text)} chars total")
            return full_text

        logger.warning("All chunks failed on Groq, trying local...")

    # Fallback: local whisper.cpp
    text = transcribe_with_local(audio_path)
    if text:
        logger.info(f"[local] Transcribed {len(text)} chars")
    return text


# --- Analysis ---

def analyze_transcript(transcript: str) -> dict | None:
    """Send transcript to Gemini API for analysis."""
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — skipping analysis")
        return None

    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        prompt_template = (PROMPTS_DIR / "meeting_analysis.txt").read_text(encoding="utf-8")
        prompt = prompt_template.replace("{transcript}", transcript)

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = response.text.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini response as JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return None


# --- Markdown rendering ---

def render_markdown(filename: str, transcript: str, analysis: dict | None, partial: str | None = None) -> str:
    """Generate the Obsidian markdown note."""
    date = datetime.now().strftime("%Y-%m-%d")
    name = Path(filename).stem

    lines = [
        f"# Reunión — {name}",
        "",
        f"**Fecha**: {date}",
        f"**Audio**: {filename}",
    ]

    if partial:
        lines.append(f"**Estado**: Procesando ({partial})")
    lines.append("")

    if analysis:
        # Resumen
        lines.append("## Resumen")
        for item in analysis.get("resumen", []):
            lines.append(f"- {item}")
        lines.append("")

        # Participantes
        if analysis.get("participantes"):
            lines.append("## Participantes")
            for p in analysis["participantes"]:
                rol = f" — {p['rol']}" if p.get("rol") else ""
                lines.append(f"- **{p.get('nombre', 'Desconocido')}**{rol}")
                for interv in p.get("intervenciones_clave", []):
                    lines.append(f"  - {interv}")
            lines.append("")

        # Temas tratados
        if analysis.get("temas_tratados"):
            lines.append("## Temas Tratados")
            for t in analysis["temas_tratados"]:
                conclusion = f" → _{t['conclusión']}_" if t.get("conclusión") else ""
                lines.append(f"### {t.get('tema', 'Sin título')}{conclusion}")
                if t.get("resumen"):
                    lines.append(f"{t['resumen']}")
                lines.append("")

        # Acuerdos
        if analysis.get("acuerdos"):
            lines.append("## Acuerdos")
            for a in analysis["acuerdos"]:
                resp = a.get("responsable", "sin asignar")
                deadline = f" (deadline: {a['deadline']})" if a.get("deadline") else ""
                lines.append(f"- ✅ {a['acuerdo']} — **{resp}**{deadline}")
            lines.append("")

        # Pendientes
        lines.append("## Pendientes")
        for p in analysis.get("pendientes", []):
            resp = p.get("responsable", "sin asignar")
            prio = f" [{p['prioridad']}]" if p.get("prioridad") else ""
            lines.append(f"- [ ] {p['tarea']} — **{resp}**{prio}")
        lines.append("")

        # Dudas abiertas
        if analysis.get("dudas_abiertas"):
            lines.append("## Dudas Abiertas")
            for d in analysis["dudas_abiertas"]:
                lines.append(f"- ❓ {d}")
            lines.append("")

        # Riesgos
        if analysis.get("riesgos"):
            lines.append("## Riesgos")
            for r in analysis["riesgos"]:
                lines.append(f"- ⚠️ {r}")
            lines.append("")

        # Seguimiento
        if analysis.get("seguimiento"):
            lines.append("## Seguimiento")
            for s in analysis["seguimiento"]:
                lines.append(f"- 📌 {s}")
            lines.append("")

        # Contexto relevante
        if analysis.get("contexto_relevante"):
            lines.append("## Contexto Relevante")
            for c in analysis["contexto_relevante"]:
                lines.append(f"- {c}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Transcript Completo")
    lines.append("")
    lines.append(transcript)
    lines.append("")

    return "\n".join(lines)


def save_note(audio_path: Path, transcript: str, analysis: dict | None, partial: str | None = None):
    """Save markdown note. Partial notes get _PARTIAL suffix."""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    suffix = "_PARTIAL" if partial else ""
    note_name = f"{date}-{audio_path.stem}{suffix}.md"
    note_path = NOTES_DIR / note_name

    markdown = render_markdown(audio_path.name, transcript, analysis, partial)
    note_path.write_text(markdown, encoding="utf-8")
    logger.info(f"Note {'updated' if partial else 'created'}: {note_path}")
    return note_path


# --- Pipeline ---

def process_file(audio_path: Path):
    """Full pipeline for a single audio file."""
    logger.info(f"Processing: {audio_path.name}")

    if not is_file_stable(audio_path):
        logger.info(f"File still being written, skipping: {audio_path.name}")
        return

    with tempfile.TemporaryDirectory(prefix="meeting_") as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Transcribe (Groq cloud or local fallback)
        transcript = transcribe_meeting(audio_path, tmpdir_path)
        if not transcript:
            logger.error(f"Transcription failed for {audio_path.name}")
            return

        logger.info(f"Transcript length: {len(transcript)} chars")

        # Analyze with Gemini
        logger.info("Analyzing with Gemini...")
        analysis = analyze_transcript(transcript)
        if analysis:
            logger.info("Analysis complete")
        else:
            logger.warning("Analysis failed — generating transcript-only note")

        # Save final note (removes any _PARTIAL)
        note_path = save_note(audio_path, transcript, analysis)

        # Clean up partial note if exists
        date = datetime.now().strftime("%Y-%m-%d")
        partial_path = NOTES_DIR / f"{date}-{audio_path.stem}_PARTIAL.md"
        if partial_path.exists():
            partial_path.unlink()

        # Archive original
        DONE.mkdir(parents=True, exist_ok=True)
        dest = DONE / audio_path.name
        shutil.move(str(audio_path), str(dest))
        logger.info(f"Archived: {dest}")


def process_inbox():
    """Process all audio files in the inbox."""
    if not INBOX.exists():
        logger.warning(f"Inbox not found: {INBOX}")
        return

    files = [
        f for f in INBOX.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_FORMATS
    ]

    if not files:
        logger.info("No audio files in inbox")
        return

    for audio_file in sorted(files):
        try:
            process_file(audio_file)
        except Exception as e:
            logger.error(f"Error processing {audio_file.name}: {e}", exc_info=True)


def main():
    logger.info("=== Meeting Transcriber v2 started ===")
    engine = "groq" if GROQ_API_KEY_MEETINGS else "local"
    logger.info(f"Engine: {engine}")
    process_inbox()
    logger.info("=== Meeting Transcriber v2 finished ===")


if __name__ == "__main__":
    main()
