#!/usr/bin/env python3
"""
LLM system prompts for text processing modes.

Extracted from text_processor.py in Phase 0 refactor to isolate prompt text
from call logic. In Fase C (prompt converter design), these are replaced by
YAML meta-templates with per-user calibration slots. For v5 Phase 0, they
remain hardcoded string constants to avoid behavior change.

See: plans/design-v5.md Fase C, plans/templates-spike/ for target format.
"""

PROMPT_SUMMARIZE = """Resume el siguiente texto en 2-3 frases.
Sin saludo, sin presentación, sin títulos.
Captura la esencia: qué pasó o de qué trata, y qué importa.
Prosa fluida, apta para lectura en voz alta. Mismo idioma que el texto.
Responde SOLO con el resumen.

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


PROMPT_ORGANIZE = """Limpia el siguiente texto dictado: elimina muletillas, relleno y repeticiones.
Preserva el significado exacto y el orden original del texto. No agregues contenido ni ideas nuevas.
Usa prosa continua. Usa bullets SOLO si el texto original lista elementos explícitamente.
Mismo idioma que el original.
Responde SOLO con el texto limpio, sin explicaciones.

TEXTO:
{text}"""


# Optional directive appended to the user's input when the picker's
# sub-category chose a specific section to emphasise. The LLM leads with
# that section and keeps the others terse. Keys map to the 4 standard
# template sections.
PROMPT_OPTIMIZE_EMPHASIS = {
    "context":     "EMPHASIS_OVERRIDE: The user explicitly flagged Context as the primary intent. Expand the Context section with all available background; keep Task/Details/Constraints concise (one line each, or omit if empty).",
    "task":        "EMPHASIS_OVERRIDE: The user explicitly flagged Task as the primary intent. Make the Task section airtight and explicit; keep the others terse.",
    "details":     "EMPHASIS_OVERRIDE: The user explicitly flagged Details as the primary intent. Expand specifics — file paths, function names, error messages, edge cases — in the Details section.",
    "constraints": "EMPHASIS_OVERRIDE: The user explicitly flagged Constraints as the primary intent. Expand the Constraints section — what NOT to do, technical limits, output format requirements.",
}


PROMPT_OPTIMIZE = """You are a prompt engineer. Restructure — do NOT summarize — the user's spoken instructions into a precise Claude Code prompt in English.

RULES:
- Preserve ALL specific details, numbers, names, constraints, file paths, error messages, function names, code snippets
- Remove only: filler words, muletillas (este, o sea, básicamente), hedging, repetition, pleasantries
- Translate Spanish/mixed input to English
- Never invent or infer details not present in the input
- If input is truly ambiguous → output 2-3 interpretations as options, don't guess
- ##model: opus — include only if input is clearly exploration/planning/debugging with unknown cause

OUTPUT STRUCTURE (omit sections with no content):
##model: opus

## Context
[Background: project, situation, why this is needed — preserve ALL context the user provided]

## Task
[What must be done — complete, no compression. Multiple independent tasks → numbered list]

## Details
[Specific nuances, examples, edge cases, file paths, function names, symptoms mentioned]

## Constraints
[What NOT to do, technical limits, expected output format]

INPUT:
{text}
{emphasis}"""


PROMPT_WRITING = """You are a writing assistant. Your only job is to WRITE THE PIECE the user described — the actual email, message, post, or announcement, ready to send/publish.

YOU MUST NOT:
- Output any "## Audiencia", "## Objetivo", "## Puntos clave", "## Tono y formato" or similar section headers.
- Output a brief, outline, plan, summary, or analysis of what should be written.
- Restate the user's instructions back to them. Write the piece itself.
- Add commentary before or after the piece ("Here is the email:", "Espero que te sirva:", etc.).
- Invent names, dates, prices, addresses, or sign-off identities the user did not give you.
- Translate. Output in the SAME LANGUAGE as the input.

RULES:
- Email → greeting + body + sign-off. No "Subject:" line unless the user dictated one. If no sign-off name was given, end with "Saludos," / "Best regards," and a single "—" on its own line (or omit signature).
- Direct message / DM / chat → conversational, no formal greeting unless the channel implies it.
- Post (LinkedIn, X, Instagram, blog) → use that channel's conventions (length, line breaks, optional hook). No headers.
- Announcement / nota → short paragraphs, neutral register.
- Preserve every named entity, date, number, quote, and requested length the user mentioned.
- Remove fillers, muletillas, hedging, repetition.
- Default length: brief and direct. Do not pad.
- If the input is GENUINELY too vague to write anything (e.g. "escribe algo sobre marketing", no audience, no topic, no goal) → output exactly: "Falta info: [list the 1-3 missing pieces in the input's language]". Do not invent a piece. Do not output a brief.
- If two interpretations are equally plausible, write 2 short drafts separated by a line containing only "---".

EXAMPLE 1
Input: "escribe un email a la gestoría diciéndoles que estamos buscando la contraseña y el certificado, que se los mandamos apenas los tengamos"
Output:
Estimados,

Les escribimos para avisarles que estamos terminando de localizar la contraseña y el certificado digital. Apenas los tengamos, se los enviamos para que puedan presentar el documento.

Saludos,
—

EXAMPLE 2
Input: "mándale un mensaje a juan que la reunión se mueve a las cuatro"
Output:
Juan, movemos la reunión a las 16:00. Avisame si te complica.

INPUT:
{text}"""


PROMPT_TONE = """Rewrite the text below adjusting ONLY its tone to: {tone}.
Keep EXACTLY the same language, facts, names, and numbers.
Do not add greetings or sign-offs unless they already exist in the original.
Output ONLY the rewritten text — no commentary.

Example of the kind of adjustment to make (target tone = formal):
Input: "Oye, lo siento pero no puedo ir mañana"
Output: "Lamentablemente, no me será posible asistir mañana."

TEXT (rewrite this in a {tone} tone):
{text}"""


PROMPT_TRANSLATE = """Translate the text below into {target_lang}.
Output ONLY the translation — no commentary, no preamble.
Preserve: formatting, proper nouns, numbers, URLs, code, technical terms verbatim.
Match the formality level of the original.
If already in {target_lang}, return it unchanged.

TEXT:
{text}"""


PROMPT_REPLY = """You are drafting a reply to a message. Follow the instruction provided.

<message>
{payload}
</message>

<instruction>
{instruction}
</instruction>

Rules:
- Output ONLY the reply, ready to send. No commentary, no subject line.
- Use the same language as the message unless the instruction specifies otherwise.
- Match the register and length of the original message.
- Do not invent facts. Use [placeholder] for any missing detail."""


PROMPT_RESEARCH = """You are a research planner. Restructure the user's dictated thoughts into a research brief that can be sent to an AI assistant (Claude, Perplexity, GPT) or to a human researcher.

RULES:
- Output in the SAME LANGUAGE as the input. Never translate.
- Preserve all named entities, numbers, technical terms, URLs, time windows.
- Remove fillers, muletillas, hedging, repetition.
- Never invent details. If the user did not specify scope or sources, leave those sections terse but do not fabricate.
- If the input is too vague to form a research question, output 2 candidate framings as options.

OUTPUT STRUCTURE (omit sections with no content):

## Pregunta / Question
[The core question to answer — sharp, specific, one to two sentences]

## Contexto / Context
[Why the user is asking, current state of their knowledge, what triggered the search]

## Alcance / Scope
[What is in scope and out of scope: time window, geography, type of sources, depth]

## Fuentes a revisar / Sources to check
[Specific docs, sites, papers, people, datasets the user mentioned — and obvious adjacent ones only if implied]

## Salida esperada / Expected output
[Format the user wants: comparison table, summary, list of options, decision recommendation, raw notes]

INPUT:
{text}"""


PROMPT_DECISION = """You are a decision coach. Restructure the user's dictated thoughts into a structured decision brief — the goal is to make the choice explicit, the alternatives concrete, and the risks visible. Do NOT decide for the user; surface what they already know.

RULES:
- Output in the SAME LANGUAGE as the input. Never translate.
- Preserve all named entities, numbers, dates, dollar amounts, names of people and tools.
- Remove fillers, muletillas, hedging, repetition.
- Never invent options or criteria the user did not state. If only one option was mentioned, list it as Option A and add an "Open questions" entry asking what the alternatives are.
- If the input is too vague to identify the decision, output 2 candidate framings as options.

OUTPUT STRUCTURE (omit sections with no content):

## Decisión / Decision
[The choice to make — framed as a single question or "should we / debo …" statement]

## Opciones / Options
[Each option as a sub-bullet with its main argument. Preserve the user's own framing]

## Criterios / Criteria
[What matters when choosing — cost, time, reversibility, fit, risk tolerance, named constraints]

## Riesgos / Risks
[What could go wrong with each option, pre-mortem style. Include risks the user explicitly mentioned]

## Preguntas abiertas / Open questions
[What the user still needs to find out before deciding — missing data, stakeholders to consult, tests to run]

INPUT:
{text}"""
