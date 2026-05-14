# auto-whisper v5 — Beta test checklist

```
┌──────────────────────────────────────────────────────────────┐
│  v5.0 GA  ·  BETA VALIDATION  ·  ROLLBACK SAFE  ·  MIT       │
└──────────────────────────────────────────────────────────────┘
```

Cambias tu LaunchAgent diario de v4.2 a v5. Todo es reversible con un comando (`make revert-to-v4`). Plan: 30-60 min sobre un día normal de trabajo.

> **Nota honesta sobre el scope (2026-04-30)**: v5.0 GA shipped. La **Phase 0** (direct Groq path) es la línea de producción — el LaunchAgent `com.auto-whisper` corre con eso desde 2026-04-28 sin crashes. El **service split FastAPI** documentado abajo ("via auto-whisper-service" banner lines, Round 1 service checks) está **diferido a v5.1** — `fastapi`/`uvicorn` no están en el venv activo, y los flags `AUTO_WHISPER_USE_SERVICE_*` no están seteados en el plist activo. Trata este archivo como (a) log histórico de validación beta, y (b) referencia para v5.1 cuando volvamos a wirear el service. Para installs nuevos de v5.0, seguí el installer doc en lugar de éste.

---

## Pre-flight · 5 min

```bash
cd /Users/stj/src/auto-whisper-v5

# Sanity: tests pasan
make test

# Sanity: v5 venv funciona
.venv/bin/python -c "import auto_whisper, auto_whisper_service; print('OK')"

# Confirma que v4.2 es tu LaunchAgent activo hoy (baseline)
make v4-status
# Esperado: línea tipo "12345  -  com.auto-whisper"
```

---

## Round 01 · manual run, sin tocar LaunchAgent · 10 min

Valida que el binario v5 funciona end-to-end antes de comprometerse a un launchd swap.

```bash
make unload-v4         # detiene v4.2 (archivo preservado, recargás con load-v4)
make run-v5-beta       # launches menubar; daemon auto-spawns service
```

### Checks mientras v5 corre

```
[ ] Banner muestra las 6 líneas:
    · Cloud engine:    via auto-whisper-service
    · LLM processing:  via auto-whisper-service
    · TTS:             via auto-whisper-service
    · Mic input:       prefer built-in over Bluetooth
    · Service:         reachable
[ ] Right-cmd-cmd → hablar → texto pegado en el cursor
[ ] Menu bar "Recent dictations" populates tras 1-2 dictados
[ ] Menu bar "Optimize what I say → prompt" produce prompt 4-secciones
[ ] Menu bar "Explain selection (speak)" efectivamente habla (TTS)
[ ] Stop button mientras habla → audio se detiene inmediato
```

### Test de regresión AirPods (el bug que cazamos)

```
[ ] Conectar AirPods
[ ] Right-cmd-cmd → hablar normal → revisar calidad de transcripción
[ ] Los logs (Ctrl+C menubar, scroll up) muestran:
    "Default input is Bluetooth (AirPods Pro); overriding to built-in
     (MacBook Air Microphone) for transcription quality."
[ ] Comparar calidad con AUTO_WHISPER_PREFER_BUILTIN_MIC=0 make run-v5-beta
    (el bug path) — debería verse notoriamente peor.
```

Cuando termines Round 01: `Ctrl+C` el menubar.

```bash
make load-v4    # restaura v4.2 como daemon activo mientras decides
```

---

## Round 02 · instalar como LaunchAgent activo · 5 min + días de uso real

Si Round 01 se vio bien, swap launchd.

```bash
make install-v5
# El output termina con el rollback command — mantené esa terminal abierta o copialo.
```

### Verificar que launchd tiene v5

```bash
make v5-status
# Esperado: "12345  0  com.auto-whisper.v5"

make v4-status
# Esperado: "v4.2 not running" (archivo en ~/Library/LaunchAgents/com.auto-whisper.plist preservado)
```

Ahora usá v5 para trabajo normal todo el tiempo que estés cómodo. El LaunchAgent activo persiste entre logins.

---

## Rollback · anytime

```bash
cd /Users/stj/src/auto-whisper-v5
make revert-to-v4
```

Esto unloads v5 y re-carga v4.2. Ambos plist se quedan en disco — flipeás a v5 con `make install-v5` cuando quieras.

---

## Logs

Tailing ayuda a diagnosticar cualquier rareza:

```bash
tail -f ~/Library/Logs/auto-whisper-v5/auto-whisper-v5.err     # menubar + service stderr
ls ~/Library/Logs/auto-whisper-v5/                              # ambos .out y .err
```

---

## Scope v5.0 GA · qué shipped vs qué está diferido

### Shipped en v5.0

- Direct Groq path con AirPods built-in-mic override.
- `optimize_prompt()` 4-section restructure, `max_tokens=1500`.
- Modos existentes: dictate, organize, optimize, summarize, explain, read.

### Diferido a v5.1

- **FastAPI service split** (el path "via auto-whisper-service" documentado en este archivo). El código está en tree y testeado aislado, pero nunca corrido en producción. Watchdog con auto-respawn en subprocess muerto/unhealthy está pre-built en `auto_whisper/service_lifecycle.py` para cuando esto se prenda.
- **Privacy Mode end-to-end** (hoy solo TTS fuerza offline; gating de transcripción/LLM llega cuando ship el service split).
- **Onboarding wizard** — TTS voice/rate hardcoded.
- **DMG / signed installer**. Phase 3 ships shell installer para demo users tech-friendly; signed bundle es concern de v5.2+ una vez validada la hypothesis del prompt-converter.

---

## Qué reportar de vuelta si algo rompe

```
[ 01 ]  Banner output de un launch fresh (make run-v5-beta foreground)
[ 02 ]  Últimas ~50 líneas de ~/Library/Logs/auto-whisper-v5/auto-whisper-v5.err
[ 03 ]  Qué hacías justo antes del failure
```

---

```
┌──────────────────────────────────────────────────────────────┐
│  auto-whisper · v5 · MIT · Hecho en El Crisol                │
└──────────────────────────────────────────────────────────────┘
```
