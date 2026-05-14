"""
Realistic dictation samples for regression testing.

These mirror the kinds of inputs auto-whisper processes daily: mixed
Spanish/English, Chilean idioms, filler words, technical terms, varying
lengths. Used across unit tests and (future) golden/snapshot tests.
"""

SAMPLES: dict[str, str] = {
    "code_refactor_es": (
        "necesito que refactores la función de transcripción en "
        "dictation_daemon punto pi, específicamente el método de "
        "audio callback. el objetivo es extraer la lógica de silence "
        "detection a un módulo aparte porque hoy está mezclada con "
        "el stream management y es difícil testear"
    ),

    "bug_report_es": (
        "hay un bug, cuando conecto los AirPods al Mac y luego los "
        "switcho al iPhone, la app se cuelga. el error es port audio "
        "minus nine nine eight six. ya pasó tres veces esta semana"
    ),

    "organize_ideas_mixed": (
        "o sea, básicamente lo que quiero es, este, armar un sistema "
        "que detecte cuando el usuario está dictando código versus "
        "texto normal y adapte el output. something like a classifier "
        "que decida entre dos paths"
    ),

    "summarize_long_es": (
        "El mercado de aplicaciones de dictado ha evolucionado "
        "significativamente en los últimos años. Inicialmente dominado "
        "por soluciones como Dragon NaturallySpeaking y la dictación "
        "nativa de Apple, ahora enfrenta competencia de startups que "
        "combinan speech-to-text con post-processing por LLM. Wispr Flow, "
        "Superwhisper y Aqua Voice son los principales entrantes. "
        "El segmento de power users paga entre diez y veinticinco dólares "
        "al mes, mientras que el mercado empresarial está desatendido "
        "con opciones limitadas en el rango de treinta a cincuenta dólares "
        "por usuario al mes. Las oportunidades clave incluyen dialectos "
        "regionales, aprendizaje de vocabulario específico de proyecto "
        "y estructuración de prompts para interacción con IA."
    ),

    "explain_target_technical": (
        "Arch-2 usa un daemon Python con FastAPI exponiendo endpoints "
        "locales sobre HTTP, con autenticación por token en Keychain. "
        "El menu bar client es thin y comunica vía httpx. IPC latency "
        "~1-2ms, cross-platform ready para iPad v6."
    ),

    "truncation_edge": "a" * 5000,  # Forces MAX_INPUT_CHARS=4000 truncation

    "short_dictation": "agrega un print al inicio",

    "empty_ish": "   ",

    "chilean_casual": (
        "cachai que el app se está poniendo fome cuando procesa audio "
        "muy largo, al tiro se pega. hay que meterle un timeout po"
    ),

    "english_only": (
        "refactor the process_selection method to extract the text "
        "capture logic into a separate function. keep the same API "
        "surface so dictation_daemon doesn't need changes"
    ),
}
