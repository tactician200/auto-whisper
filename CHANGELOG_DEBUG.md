# Auto-Whisper — Debug Changelog

Historial de diagnósticos y fixes. Más reciente primero.

---

## [2026-04-30] v5.0 GA — Phase 0 (direct Groq path)

**Cierre de v5:** marcado GA tras 2 días de daily-driving sin crashes, errores ni warnings observados en `dictation.log`.

**Scope shipped:**
- Binary v5 reemplazando a v4.2 como LaunchAgent activo (label `com.auto-whisper`, plist symlinkado al repo v5).
- Path direct (Groq → daemon → paste). FastAPI service split queda escrito pero **diferido a v5.1** — nunca corrió en producción, fastapi/uvicorn no instalados en venv activo. Razón: YAGNI para demos, mejor shipear lo validado que estrenar capa nueva.
- AirPods accuracy fix activo (`AUTO_WHISPER_PREFER_BUILTIN_MIC=1` por defecto en plist, override built-in mic confirmado en logs y por validación del operador).
- `optimize_prompt()` con max_tokens=1500 + restructure-not-summarize (heredado de 2026-04-16).

**Diferido a v5.1:**
- Service split full (instalar fastapi, smoke real del cliente/servicio, watchdog que ya está escrito en `service_lifecycle.py`).
- Privacy mode end-to-end (transcripción/LLM gating; hoy solo TTS offline).
- DMG firmado / notarización (instalador shell sirve para demo tech-friendly).

**Watchdog de servicio:** código añadido a `service_lifecycle.py` (idempotent start, respawn on dead proc, terminate-then-respawn on health-fail). 2 tests unitarios. Inactivo hasta que v5.1 instale fastapi y active el servicio.

**Lección:** verificar el estado de runtime real antes de iterar — el plist activo tenía label v4 pero apuntaba a binary v5 sin flags de servicio. Asumir que "v5 está corriendo" = "service split está corriendo" hubiera llevado a 4-5 sesiones de scope creep.

---

## [2026-04-05] PortAudio deadlock por AirPods device-change storm

**Síntoma:** App colgada ~08:12, no responde a hotkey, proceso vivo pero sin función
**Causa raíz:** 3 vectores de hang encadenados:
1. `sd.InputStream()` constructor bloqueado indefinidamente — CoreAudio inestable por 5 device-change events en 20 min (AirPods switching Mac/iPad/iPhone)
2. `sd._terminate()` deadlock — llamado desde main thread mientras otro thread bloqueado en PortAudio
3. Sin detección proactiva — fixes anteriores solo se activaban con interacción del usuario

**Fix:** Robustness layer v4.2:
- `_create_stream_with_timeout()` — InputStream en thread con timeout 5s
- `_safe_portaudio_reset()` — terminate/initialize con timeout 5s, `os._exit(1)` si deadlock
- Watchdog thread (10s interval): processing stuck >60s → restart, no callbacks >8s → auto-stop
- Heartbeat en `_audio_callback` (`_last_callback_time`)

**Commit:** 09214cf
**Lección:** CoreAudio puede bloquear indefinidamente en constructores C — nunca llamar `sd.InputStream()` ni `sd._terminate()` sin timeout wrapper. `os._exit(1)` + launchd restart es válido como último recurso.
**Relacionado:** AirPods analysis — System Default es el config correcto; mid-recording disconnect cubierto por watchdog; -9986 en transición falla rápido (aceptable)
