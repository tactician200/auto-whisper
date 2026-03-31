#!/usr/bin/env python3
"""
Text processing module — summarize, explain, and organize spoken ideas via Groq LLM.
"""

import logging
import re
from shared.config import GROQ_API_KEY_DICTATION

logger = logging.getLogger(__name__)

PROMPT_SUMMARIZE = """Resume en 2-3 frases. Sin saludo, sin introducción, sin "el texto dice".
Solo la esencia — lo que importa, directo.
Español fluido, apto para escuchar.

TEXTO:
{text}"""

PROMPT_EXPLAIN_VOICE = """Explica esto en voz alta. Sin saludo. Sin "vamos a ver" ni "déjame explicarte". Sin resumen al final.
Ve al punto: idea central, por qué importa, qué implica — solo lo que el contenido merezca, sin anunciarlo.
Integra términos técnicos dentro del flujo sin pausas pedagógicas.
Largo adaptado: texto simple → 2-3 oraciones; complejo → lo que necesite.
Español conversacional, sin markdown ni viñetas.

TEXTO:
{text}"""

PROMPT_EXPLAIN_PASTE = """Explica como colega senior. Sin saludo ni introducción.
Idea central → por qué importa → qué implica. Solo lo que el texto merezca.
Usa estructura (bullets, párrafos cortos) solo si el contenido lo justifica — no por defecto.
Español claro y preciso.

TEXTO:
{text}"""

PROMPT_ORGANIZE = """Convierte esto en texto limpio, ordenado y listo para usar.

- Mantén el significado exacto. No agregues contenido.
- Elimina muletillas, repeticiones y ruido verbal.
- Ordena lógicamente. Bullets para puntos múltiples, numeración para pasos/acciones.
- Si ya está bien escrito, solo limpia formato.
- Devuelve solo el resultado.

TEXTO:
{text}"""


_groq_client = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        _groq_client = Groq(api_key=GROQ_API_KEY_DICTATION)
    return _groq_client


def _call_groq(prompt: str) -> str | None:
    """Call Groq LLM for text processing."""
    if not GROQ_API_KEY_DICTATION:
        logger.error("No Groq API key configured")
        return None
    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_completion_tokens=2000,
        )
        text = response.choices[0].message.content.strip()
        logger.info(f"LLM response ({len(text)} chars): {text[:100]}...")
        return text
    except Exception as e:
        logger.error(f"Groq LLM failed: {e}")
        return None


def summarize(text: str) -> str | None:
    """Summarize text. Returns voice-ready text."""
    return _call_groq(PROMPT_SUMMARIZE.format(text=text[:4000]))


def explain(text: str, for_voice: bool = True) -> str | None:
    """Explain text. for_voice=True: conversational, no markdown. False: structured for paste."""
    prompt = PROMPT_EXPLAIN_VOICE if for_voice else PROMPT_EXPLAIN_PASTE
    return _call_groq(prompt.format(text=text[:4000]))


def organize_ideas(text: str) -> str | None:
    """Turn rough dictated ideas or clipboard text into organized notes ready to paste."""
    return _call_groq(PROMPT_ORGANIZE.format(text=text[:4000]))


def notify(title: str, message: str):
    """Show macOS notification."""
    try:
        import subprocess
        # Clean any markdown/tags from message
        clean = re.sub(r'[*_#`\[\]()]', '', message)
        clean = clean.replace('"', "'").replace("\\", "")[:200]
        script = f'display notification "{clean}" with title "{title}"'
        subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True, timeout=5,
        )
    except Exception as e:
        logger.warning(f"Notification failed: {e}")
