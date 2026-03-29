#!/usr/bin/env python3
"""
Text processing module — summarize, explain, read via Groq LLM.
Splits output into voice (narrative) and data (visual) channels.
"""

import logging
import re
from shared.config import GROQ_API_KEY_DICTATION

logger = logging.getLogger(__name__)

PROMPT_SUMMARIZE = """Eres un asistente ejecutivo. Comunica la esencia de este texto como lo haría \
un profesional en una conversación breve. Prioriza: decisiones, acciones \
requeridas, y conclusiones. Omite contexto obvio y relleno.

Reglas de formato:
- Datos concretos (cifras, fechas, nombres, URLs): márcalos con [DATA]...[/DATA]
- Palabras o frases clave que requieren énfasis: márcalas con [E]...[/E]
- Agrega [P] donde corresponda una pausa natural (cambio de tema, antes de algo importante)
- Habla en español, directo. No uses muletillas como "en resumen" o "básicamente"

TEXTO:
{text}"""

PROMPT_EXPLAIN = """Explica este texto como un colega senior que domina el tema. Identifica \
la idea central, por qué importa, y qué implica. Si hay jerga técnica, \
tradúcela a lenguaje claro sin perder precisión.

Reglas de formato:
- Datos concretos: márcalos con [DATA]...[/DATA]
- Palabras o frases clave: márcalas con [E]...[/E]
- Agrega [P] donde corresponda una pausa natural
- Habla en español, claro y directo

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
    """Call Groq LLM (Llama 3.3 70B) for text processing."""
    if not GROQ_API_KEY_DICTATION:
        logger.error("No Groq API key configured")
        return None
    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_completion_tokens=1000,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Groq LLM failed: {e}")
        return None


def split_voice_data(text: str) -> tuple[str, str | None]:
    """
    Split LLM response into voice text and data text.
    [DATA]...[/DATA] tags go to data channel.
    """
    data_parts = re.findall(r'\[DATA\](.*?)\[/DATA\]', text, re.DOTALL)
    voice_text = re.sub(r'\[DATA\].*?\[/DATA\]', '', text, flags=re.DOTALL).strip()
    # Clean up double spaces/newlines from removed data tags
    voice_text = re.sub(r'\n{3,}', '\n\n', voice_text)
    voice_text = re.sub(r'  +', ' ', voice_text)

    # Clean any dangling/unclosed tags from truncated LLM output
    voice_text = re.sub(r'\[/?(?:DATA|E|P)\]', '', voice_text).strip()

    data_text = "\n".join(d.strip() for d in data_parts) if data_parts else None
    return voice_text, data_text


def summarize(text: str) -> tuple[str, str | None]:
    """Summarize text. Returns (voice_text, data_text_or_none)."""
    result = _call_groq(PROMPT_SUMMARIZE.format(text=text[:4000]))
    if not result:
        return "", None
    return split_voice_data(result)


def explain(text: str) -> tuple[str, str | None]:
    """Explain text. Returns (voice_text, data_text_or_none)."""
    result = _call_groq(PROMPT_EXPLAIN.format(text=text[:4000]))
    if not result:
        return "", None
    return split_voice_data(result)


def notify(title: str, message: str):
    """Show macOS notification."""
    try:
        import subprocess
        # Using osascript for native notification
        script = f'display notification "{_escape_applescript(message)}" with title "{_escape_applescript(title)}"'
        subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True, timeout=5,
        )
    except Exception as e:
        logger.warning(f"Notification failed: {e}")


def _escape_applescript(text: str) -> str:
    """Escape text for AppleScript strings."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
