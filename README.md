# auto-whisper

```
┌──────────────────────────────────────────────────────────────┐
│  v5.0 BETA  ·  DICTADO SUB-1s  ·  MIT  ·  MACOS 14+          │
└──────────────────────────────────────────────────────────────┘
```

**Hablas el prompt. Aparece estructurado.**

Doble-tap ⌘ derecho. Hablas en español, aparece en tu cursor un prompt en inglés con `## Context / ## Task / ## Details / ## Constraints` — listo para pegar en Claude Code, Cursor o ChatGPT. Sub-segundo en cloud, fallback local offline, y un clasificador inteligente que decide entre dictado raw, prompt para IA, redacción o brief de investigación/decisión.

> Hecho para devs e operadores que dictan en dos idiomas todo el día y quieren convertir voz cruda en artefacto utilizable (email, prompt, brief) sin soltar el teclado.

---

## Lo que dices y lo que Claude lee, no son lo mismo

Tú hablas con titubeos, el archivo mencionado a medias, el detalle al final. Claude responde mejor cuando recibe Context, Task, Details y Constraints separados. Esa traducción la haces tú, a mano, cada prompt.

`auto-whisper` la hace en sub-1 segundo. No resume. **Reordena.**

```
┌─ INPUT · VOZ · ES · 14.2s ──────────────────────────────────┐
│                                                              │
│  "Hola, tengo un bug raro en la autenticación...             │
│   en auth.ts, cuando refresca el token me da 401,            │
│   pero solo si recargo la página, no si navego               │
│   normal. No quiero tocar cómo guardo los tokens,            │
│   solo entender qué pasa."                                   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                              │
                              │  Optimize · ES → EN · 0.81s
                              ▼
┌─ OUTPUT · PROMPT · EN ──────────────────────────────────────┐
│                                                              │
│  ## Context                                                  │
│  Authentication flow on auth.ts.                             │
│                                                              │
│  ## Task                                                     │
│  Debug why the refresh token call returns 401.               │
│                                                              │
│  ## Details                                                  │
│  - File: auth.ts                                             │
│  - Symptom: 401 on token refresh                             │
│  - Trigger: page reload                                      │
│                                                              │
│  ## Constraints                                              │
│  Preserve existing token storage logic.                      │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

[ UN ATAJO · MISMO AUDIO · LISTO PARA CLAUDE CODE / CURSOR / CHATGPT ]

---

## Dos atajos. Dos intenciones.

Una hotkey dicta lo que dices. Otra interpreta para qué. El mismo motor escucha distinto según cómo lo invocas.

### Atajo 01 · Dictado preciso — `⌘⌘`

**Hablas, aparece.** Doble-tap ⌘ derecho. Inglés y español con el mismo motor Whisper Large v3. Sale limpio — el sistema quita muletillas automáticamente. Funciona donde esté tu cursor: Slack, mail, doc, editor de código.

- Bilingüe ES + EN, mismo motor
- Auto-clean del dictado
- Cualquier app de macOS

### Atajo 02 · Dictado inteligente — `⌥ + ⌘⌘`

**Hablas, el sistema interpreta para qué.** Opt + doble-tap ⌘ derecho. Un clasificador (heurísticas-first, fallback LLM para casos ambiguos) lee tu intención y entra al modo que aplica:

| Modo interno | Salida |
|---|---|
| `optimize → coding` | Prompt 4-secciones (`## Context / ## Task / ## Details / ## Constraints`) |
| `optimize → writing` | Email, mensaje o post — la pieza real, no un brief |
| `optimize → research brief` | `## Question / Context / Scope / Sources / Expected output` |
| `optimize → decision brief` | `## Decision / Options / Criteria / Risks / Open questions` |

**Reformat last**: si el clasificador eligió mal, reaplica otro modo al mismo audio sin volver a dictar.

---

## Atajos secundarios — sobre el portapapeles

| Acción | Atajo |
|---|---|
| Leer portapapeles en voz alta | doble-tap ⌘ izquierdo |
| Explicar portapapeles con contexto | `⌥` + doble-tap ⌘ izquierdo |
| Resumir / organizar última copia | desde el menú |

---

## Catálogos de prompts — habla como tu profesión

Los prompts viven en [`shared/prompts.py`](shared/prompts.py) y son hackeables. La arquitectura soporta catálogos por dominio — cada uno con vocabulario, estructura y constraints propios:

```python
# shared/prompts.py — ejemplo de catálogo dev
PROMPT_OPTIMIZE_CODING = """
You are reorganizing a developer's dictated note into a structured prompt.
Sections: ## Context / ## Task / ## Details / ## Constraints.
Preserve all technical detail. Translate to English if input is mixed.
"""

# Añadí el tuyo: médico, legal, marketing, investigación...
PROMPT_OPTIMIZE_MEDICAL = """..."""
PROMPT_OPTIMIZE_LEGAL   = """..."""
```

Cada catálogo es un set de prompts ajustados a un dominio. Activas el que aplica a tu trabajo y el output sale calibrado, no genérico. Tu catálogo vive en `~/.config/auto-whisper/` — versionado contigo, no en una nube de terceros.

**Roadmap**: 6 catálogos pre-built (dev, médico, legal, marketing, investigación, personalizado) — hoy editas `shared/prompts.py` directamente; UI de selección llega en v5.1.

---

## A veces no quieres que tu audio salga del Mac

Un switch (`AUTO_WHISPER_PRIVACY_MODE=1`). Transcripción 100% local con [whisper.cpp](https://github.com/ggerganov/whisper.cpp). Voz de salida 100% offline con la voz del sistema (`say`). Sin red. Auditable en el código.

> **Nota honesta**: el procesamiento con IA (organizar, optimize, resumir, explicar) hoy corre en la nube. El modo privacidad lo bloquea cuando está activo — caes a transcripción raw + TTS local. Procesamiento LLM 100% local llega en v5.1.

---

## Quickstart

```bash
git clone https://github.com/tactician200/auto-whisper.git ~/auto-whisper
cd ~/auto-whisper
bash install.sh
```

El installer:

1. Verifica macOS 13+, Python 3.11+, Homebrew.
2. Instala `ffmpeg`.
3. Crea un venv de Python e instala dependencias.
4. Te pide tu [Groq API key gratis](https://console.groq.com/keys) (~8h/día en free tier).
5. Instala y carga un LaunchAgent — la app arranca con el login.
6. Guía el grant one-time de Microphone + Accessibility.

Desinstalar: `cd ~/auto-whisper && bash uninstall.sh`.

```
INSTALACIÓN · 4 PASOS
─────────────────────
PASO 01 → bash install.sh
PASO 02 → grant Accesibilidad + Micrófono
PASO 03 → pegar Groq API key (free tier)
PASO 04 → doble-tap ⌘ derecho, hablar
```

---

## Menú (◎ en menubar)

```
Cloud · ES                           ← engine + idioma activo
▓░░░░ 1% Groq                        ← uso diario del free tier
─────
Hold ⌘⌘ to dictate
─────
Optimize last copy → prompt
Organize last copy
Summarize last copy → speak
Explain last copy → speak
Read last copy aloud
─────
Recent dictations ▶
Reformat last…              ▶ As coding prompt
                              As writing
                              As research brief
                              As decision brief
                              As organize
                              Raw (re-paste original)
Paste last again
─────
Settings ▶
  ├─ Voice modes ▶
  ├─ Engine ▶ (Cloud / Local / Auto)
  ├─ Language ▶ (ES / EN / Auto)
  ├─ Input ▶ (audio devices)
  ├─ Output: Speak / Paste
  ├─ Vocabulary ▶ (Project / Add term)
  └─ Stop speaking
```

---

## Arquitectura (v5)

v5 splits into two processes vía strangler-fig migration off del monolito v4.2:

```
┌─ menubar daemon (auto_whisper) ──────────────────────────┐
│  · global hotkey, recording, paste injection, HUD, menu  │
│  · routes LLM calls through dispatchers                  │
└────────────────────────────┬─────────────────────────────┘
                             │ HTTP (localhost only)
                             ▼
┌─ local service (auto_whisper_service) ───────────────────┐
│  · FastAPI on 127.0.0.1                                  │
│  · /transcribe (audio → text)                            │
│  · /process    (text → mode-specific LLM output)         │
│  · /tts        (text → audio)                            │
│  · /health · /version                                    │
└──────────────────────────────────────────────────────────┘
```

Flags (env vars) gate each route:
- `AUTO_WHISPER_USE_SERVICE=1` — transcription via local service
- `AUTO_WHISPER_USE_SERVICE_PROCESSING=1` — LLM calls via service
- `AUTO_WHISPER_USE_SERVICE_TTS=1` — TTS via service
- `AUTO_WHISPER_AUTOSTART_SERVICE=1` — daemon spawns the service subprocess

Los cuatro están pre-set en `com.auto-whisper.v5.plist` para producción.

---

## Comparativa honesta — no cambió el dictado, cambió cuánto dependes de pegarle texto a una IA

Hay buenas apps de dictado en Mac. Wispr Flow tiene el onboarding más pulido, Superwhisper el set de modos más extenso, MacWhisper un precio bajo de una sola vez. Cada una resuelve algo distinto.

Esta tabla no está para que cambies de herramienta. Está para que veas en qué columna cae cada una cuando el trabajo es voz → prompt.

| Feature | auto-whisper | Superwhisper | Wispr Flow | MacWhisper |
|---|---|---|---|---|
| Open source | ✅ MIT | ❌ | ❌ | ❌ |
| Precio | Gratis · BYOK Groq | Paid one-time | Subscription [verificar] | Free + paid pro |
| Cloud + local fallback | ✅ Groq + whisper.cpp | ✅ [verificar] | Cloud-only [verificar] | ✅ |
| Smart classifier → mode auto-route | ✅ heuristics + LLM | ❌ manual | Partial [verificar] | ❌ |
| Built-in prompt-for-AI mode | ✅ coding / writing / research / decision | ❌ generic "AI mode" [verificar] | ❌ | ❌ |
| Reformat last under another mode | ✅ | ❌ | ❌ | ❌ |
| Catálogos de prompts hackeables | ✅ `shared/prompts.py` | ❌ | ❌ | Partial |
| Custom vocabulary | ✅ per-project | ✅ | ✅ | ✅ |
| Privacy mode (no cloud) | ✅ env flag | Partial [verificar] | ❌ | ✅ local-only |
| Customizable hotkeys | Parcial (code-level) | ✅ | ✅ | ✅ |
| Distribución | Source + ad-hoc .app | Signed + notarized | Signed + notarized | Signed + notarized |

> Items marcados `[verificar]` son claims de competidores que no validé personalmente — PRs welcome.

**Por qué pick auto-whisper sobre un app paid pulido**: si dictas prompts de IA frecuentemente, escribes emails de cliente en dos idiomas, o quieres que los templates de prompt matcheen *tu* workflow (no el de un vendor), la hackeabilidad y el classifier mode-aware son el gap real. Si solo quieres dictado one-click sin editar, un paid app es más rápido de setup.

---

## Para equipos — 3-50 personas

¿Lo descubriste porque un dev del equipo ya la usa, y el resto pregunta?

No hay nada que comprar — es open source MIT, la API key la pone cada usuario. Lo que sí ofrecemos para equipos de 3 a 50 personas es despliegue con menos fricción que un README: setup guiado, vocabulario compartido por proyecto, catálogos de prompts personalizados al dominio del equipo, privacy mode auditado con NDA si tu legal lo pide.

Frente a $144/usuario/año de Wispr Flow o Willow Voice, la cuenta no requiere deck.

Contacto: [tactician.200@gmail.com](mailto:tactician.200@gmail.com) — respuesta en 48h.

---

## Packaging .app

```bash
.venv/bin/pip install py2app
.venv/bin/python setup_app.py py2app
codesign --force --deep --sign - dist/AutoWhisper.app
```

Resultado: `dist/AutoWhisper.app` (~105 MB, LSUIElement menubar app).

Ad-hoc signing funciona en la build machine. Para distribución a otros usuarios, reemplaza `--sign -` con tu Developer ID y notariza vía `xcrun notarytool`.

---

## Commands

```bash
# Restart daemon (nota: puede dejar service hijo con código stale —
# verifica con `ps -ef | grep auto_whisper`)
launchctl kickstart -k gui/$(id -u)/com.auto-whisper.v5

# Tail runtime log
tail -f ~/Library/Logs/auto-whisper/dictation.log

# Edit API key / config
nano ~/auto-whisper/.env

# Tests
make test
```

---

## Troubleshooting

- **No pasa nada al hotkey** → re-grant Accessibility para el Python binary del venv en System Settings → Privacy & Security → Accessibility.
- **Mic no captura con AirPods conectados** → intencional. AirPods accuracy es pobre para dictado; overrideamos a built-in mic.
- **Edits de prompt no toman efecto al restart** → kill el service process (`pkill -f auto_whisper_service`) y `launchctl kickstart -k` el daemon. El service puede sobrevivir un restart de daemon y mantener prompts stale en memoria.
- **Groq quota hit** → switch a Local engine en Settings, o esperar daily reset.

---

## License

MIT — ver [`LICENSE`](LICENSE).

```
┌──────────────────────────────────────────────────────────────┐
│  auto-whisper · v5 · MIT · Hecho en El Crisol                │
└──────────────────────────────────────────────────────────────┘
```
