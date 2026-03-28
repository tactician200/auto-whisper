#!/usr/bin/env python3
"""
Meeting Transcription Pipeline
Watches ~/MeetingInbox/ for audio files, transcribes with whisper.cpp,
analyzes with Gemini API, and generates Obsidian markdown notes.
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from shared.config import (
    INBOX, DONE, LOGS, NOTES_DIR,
    SUPPORTED_FORMATS, GEMINI_API_KEY, GEMINI_MODEL, PROMPTS_DIR,
)
from shared.whisper_runner import convert_audio, transcribe_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS / "transcriber.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def is_file_stable(path: Path, wait_seconds: int = 3) -> bool:
    """Check if file is done being written (size stable)."""
    size1 = path.stat().st_size
    time.sleep(wait_seconds)
    size2 = path.stat().st_size
    return size1 == size2 and size1 > 0


def analyze_transcript(transcript: str) -> dict | None:
    """Send transcript to Gemini API for analysis."""
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — skipping analysis")
        return None

    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        prompt_template = (PROMPTS_DIR / "meeting_analysis.txt").read_text()
        prompt = prompt_template.replace("{transcript}", transcript)

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = response.text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
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


def render_markdown(filename: str, transcript: str, analysis: dict | None) -> str:
    """Generate the Obsidian markdown note."""
    date = datetime.now().strftime("%Y-%m-%d")
    name = Path(filename).stem

    lines = [
        f"# Reunion — {name}",
        "",
        f"**Fecha**: {date}",
        f"**Audio**: {filename}",
        "",
    ]

    if analysis:
        # Resumen
        lines.append("## Resumen")
        for item in analysis.get("resumen", []):
            lines.append(f"- {item}")
        lines.append("")

        # Pendientes
        lines.append("## Pendientes")
        for p in analysis.get("pendientes", []):
            resp = p.get("responsable", "sin asignar")
            lines.append(f"- [ ] {p['tarea']} ({resp})")
        lines.append("")

        # Temas Clave
        lines.append("## Temas Clave")
        for t in analysis.get("temas_clave", []):
            lines.append(f"- {t}")
        lines.append("")

        # Decisiones
        lines.append("## Decisiones")
        for d in analysis.get("decisiones", []):
            lines.append(f"- {d}")
        lines.append("")

        # Analisis de Negocio
        biz = analysis.get("analisis_negocio", {})
        lines.append("## Analisis de Negocio")
        if biz.get("oportunidades"):
            lines.append("**Oportunidades:**")
            for o in biz["oportunidades"]:
                lines.append(f"- {o}")
        if biz.get("riesgos"):
            lines.append("**Riesgos:**")
            for r in biz["riesgos"]:
                lines.append(f"- {r}")
        if biz.get("compromisos"):
            lines.append("**Compromisos:**")
            for c in biz["compromisos"]:
                lines.append(f"- {c}")
        if biz.get("proximos_pasos"):
            lines.append("**Proximos pasos:**")
            for p in biz["proximos_pasos"]:
                lines.append(f"- {p}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Transcript Completo")
    lines.append("")
    lines.append(transcript)
    lines.append("")

    return "\n".join(lines)


def process_file(audio_path: Path):
    """Full pipeline for a single audio file."""
    logger.info(f"Processing: {audio_path.name}")

    # Check file is done writing
    if not is_file_stable(audio_path):
        logger.info(f"File still being written, skipping: {audio_path.name}")
        return

    # Create temp working directory
    with tempfile.TemporaryDirectory(prefix="meeting_") as tmpdir:
        tmpdir = Path(tmpdir)

        # Stage 2: Prepare — convert audio
        wav_path = tmpdir / "raw.wav"
        logger.info("Converting audio...")
        if not convert_audio(audio_path, wav_path):
            logger.error(f"Audio conversion failed for {audio_path.name}")
            return

        # Stage 3: Transcribe
        transcript_path = tmpdir / "transcript"
        logger.info("Transcribing with whisper...")
        transcript = transcribe_file(wav_path, transcript_path)
        if not transcript:
            logger.error(f"Transcription failed for {audio_path.name}")
            return

        logger.info(f"Transcript length: {len(transcript)} chars")

        # Stage 4: Analyze
        logger.info("Analyzing with Gemini...")
        analysis = analyze_transcript(transcript)
        if analysis:
            # Save analysis JSON for debugging
            (tmpdir / "analysis.json").write_text(
                json.dumps(analysis, ensure_ascii=False, indent=2)
            )
            logger.info("Analysis complete")
        else:
            logger.warning("Analysis failed — generating transcript-only note")

        # Stage 5: Render
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        date = datetime.now().strftime("%Y-%m-%d")
        note_name = f"{date}-{audio_path.stem}.md"
        note_path = NOTES_DIR / note_name

        markdown = render_markdown(audio_path.name, transcript, analysis)
        note_path.write_text(markdown)
        logger.info(f"Note created: {note_path}")

        # Stage 6: Archive
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


if __name__ == "__main__":
    logger.info("=== Meeting Transcriber started ===")
    process_inbox()
    logger.info("=== Meeting Transcriber finished ===")
