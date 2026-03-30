#!/usr/bin/env python3
"""
Text processing module — summarize, explain, and organize spoken ideas via Groq LLM.
"""

import logging
import re
from shared.config import GROQ_API_KEY_DICTATION

logger = logging.getLogger(__name__)

PROMPT_SUMMARIZE = """Di la esencia de este texto en 2-3 frases cortas. Solo lo que importa. \
Nada de contexto, introducción ni explicaciones. Como un titular expandido.
Español, directo, sin muletillas.

TEXTO:
{text}"""

PROMPT_EXPLAIN = """Explica este texto como un colega senior que domina el tema.

Reglas:
- Identifica la idea central, por qué importa, y qué implica
- Si hay jerga técnica, tradúcela a lenguaje claro sin perder precisión
- Habla en español, claro y directo
- Escribe como si fueras a leerlo en voz alta — fluido y conversacional

TEXTO:
{text}"""

PROMPT_ORGANIZE_IDEAS = """Convierte esta dictación en un texto claro, ordenado y listo para pegar.

Reglas:
- Mantén el significado original. No inventes hechos ni detalles.
- Limpia muletillas, repeticiones, frases a medio cerrar y ruido verbal.
- Ordena las ideas de forma lógica.
- Si hay varios puntos, usa viñetas.
- Si hay pasos o acciones, usa una lista numerada.
- Si el contenido ya está bastante claro, solo mejora redacción y estructura.
- Escribe en español claro, directo y útil.
- Devuelve solo el resultado final.

DICTACIÓN:
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
            max_completion_tokens=800,
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


def explain(text: str) -> str | None:
    """Explain text. Returns voice-ready text."""
    return _call_groq(PROMPT_EXPLAIN.format(text=text[:4000]))


def organize_ideas(text: str) -> str | None:
    """Turn rough dictated ideas into organized notes ready to paste."""
    return _call_groq(PROMPT_ORGANIZE_IDEAS.format(text=text[:4000]))


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
