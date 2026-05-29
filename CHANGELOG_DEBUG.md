# Auto-Whisper — Debug Changelog

Historial de diagnósticos y fixes. Más reciente primero.

---

## [2026-05-29] HUD parece colgado al procesar + latencia alta por timeout Groq de 30s

**Síntoma:** al terminar de hablar, el HUD muestra el waveform **parado** pero el timer sigue corriendo, y el chip sigue diciendo el modo (`DICTATE`) — parece colgado. Además algunas transcripciones tardan mucho (hasta ~41s) pese a usar Groq.

**Causa raíz (2 problemas):**
1. **UI:** el timer corría a 60fps desde `_show_time` hasta `hide()`, sin congelarse al pasar a procesamiento. El waveform dejaba de recibir `push_level` (grabación terminada) → bars congeladas en el último RMS. Sin estado visual de "procesando".
2. **Latencia:** timeout fijo de 30s en el cliente Groq (`shared/groq_client.py`) + en `ServiceClient.transcribe`. Datos del log: la transcripción normal toma 0.6–1.5s, pero el 2026-05-29 la API de Groq se degradó (5.9–9.3s) y a las 11:52 colgó hasta el techo exacto de 30s → recién ahí arrancó el fallback local whisper.cpp (+11s) = ~41s totales. El techo de 30s es absurdo para dictado interactivo.

**Fix:**
- `ui/floating_window.py`: nuevo `mark_processing()` — congela el duration label en su último valor, oculta el dot/label REC, cambia el ModeChip a `PROCESANDO`. Flag `_processing_ui` hace que `_tick` no avance el timer y dispare un barrido indeterminado. Reset en `show()`/`hide()`.
- `ui/waveform_view.py`: `pulse_indeterminate(phase)` — bump gaussiano que va y viene (ping-pong ~1.4s) para que lea como "pensando", no colgado.
- `dictation_daemon.py`: `_stop_and_transcribe()` llama `_hud.mark_processing()` al iniciar el procesamiento.
- `transcription.py`: nuevo `cloud_timeout_for(dur) = min(30, max(10, dur*0.5))`. Aplicado al `ServiceClient.transcribe(timeout=)` (lado daemon = ceiling real) y al path directo Groq vía `client.with_options(timeout=)`. Clips cortos colgados ahora caen al fallback en ~10s en vez de 30s. Piso de 10s protege llamadas lentas-pero-válidas (~9s observadas).

**Límite conocido:** cuando Groq genuinamente cuelga, igual se paga el fallback local (~11s con modelo `medium`, beam 8). El fix acelera la *caída* al fallback, no la API de Groq. No toqué el proceso service (evita el reload-trap del child con `kickstart -k`).

**Validación:** ✅ confirmada en uso (2026-05-29) — rebuild del bundle + reinstall + re-grant TCC (el re-sign ad-hoc revocó Accessibility/Microphone, resuelto con `tccutil reset … com.auto-whisper.app` + re-grant manual). Usuario reporta latencia baja y el estado `PROCESANDO` visible al terminar de hablar.

**Lección:** timeouts de red en flujos interactivos deben escalar con el tamaño del payload, no ser fijos. Un techo de 30s en una llamada que normalmente toma 1s convierte cualquier degradación de la API en 30s de espera visible.

**Relacionado:** [memory: service reload trap](feedback_service_reload.md).

---

## [2026-05-24] HUD desaparece tras multi-day uptime (sleep/wake invalida NSPanel)

**Síntoma:** después del fix del 2026-05-20, el HUD volvió a no aparecer al cabo de ~3 días de uptime continuo del daemon. `kickstart -k` lo arreglaba pero el bug recurría. Los logs mostraban grabación arrancando limpia (`Recording started (dictate) at 16000 Hz` <100ms post-hotkey) sin excepciones — el daemon estaba sano, solo el render del HUD fallaba.

**Causa raíz (hipótesis #1 de 3, validada):** macOS WindowServer invalida silenciosamente la registración de `NSPanel` con `NSStatusWindowLevel + NSWindowCollectionBehaviorCanJoinAllSpaces` después de deep sleep. `orderFrontRegardless()` retorna éxito a nivel Cocoa pero el panel ya no existe en el window list del WindowServer — el usuario nunca lo ve. Mac uptime de 23 días + sleep diario = condiciones perfectas para acumular esta corrupción.

**Fix:**
- `ui/floating_window.py`: agregada clase `_HUDWakeObserver` (NSObject ObjC-bridged) que se subscribe a `NSWorkspace.didWakeNotification`. Al despertar el Mac, setea `self._panel_stale = True` en el HUD.
- Nuevo método `_teardown_panel()` — invalida timer, hace `orderOut_`, nullea todos los refs de subviews.
- `_show_main()` chequea `_panel_stale` antes de cualquier acción: si está set, llama `_teardown_panel()` → próximo `_build_panel()` crea NSPanel fresco que el WindowServer re-registra correctamente.

**Logs nuevos para diagnosticar futuras recurrencias:**
- `[HUD] Wake observer registered` — al startup
- `[HUD] System woke from sleep — marking panel stale for next show()` — al wake
- `[HUD] Panel marked stale (post-wake) — rebuilding` — en el próximo show post-wake

**Validación:** pendiente. Si después de 5+ días de uso continuo (con sleep diario, switches Mac↔AirPods, screensaver, etc.) el HUD sigue apareciendo, la causa raíz era #1. Si vuelve a fallar, atacar #2 (race show/hide < FADE_OUT_MS+50ms) o #3 (NSScreen.mainScreen() stale tras display reconfig).

**Lección:** PyObjC menubar apps con `NSStatusWindowLevel` + `CanJoinAllSpaces` deben observar `NSWorkspace.didWakeNotification` y reconstruir panels post-wake. Es un workaround conocido pero raramente documentado. Otros menubar apps grandes (Bartender, Hand Mirror, etc.) lo hacen por la misma razón.

---

## [2026-05-20] HUD no aparece (intermitente, peor con AirPods)

**Síntoma:** double-tap ⌘⌘ no muestra el HUD; problema más frecuente con AirPods conectados.

**Causa raíz:** `_hud.show()` se llamaba **después** de `stream.start()` dentro del thread de fondo de `_start_recording()`. Con Bluetooth conectado, macOS fuerza handshake HFP/SCO al abrir el mic — eso puede tardar 1–3s o disparar `TimeoutError` tras 5s (`STREAM_OPEN_TIMEOUT`). En el camino lento el HUD aparecía tarde; en el camino de error, nunca aparecía (el `except` solo seteaba "Mic error" en el menubar).

**Fix:**
- `ui/floating_window.py`: agregado flag `_preparing`. `show()` ahora monta el panel pero esconde REC label + dot. Nuevo método `mark_recording_started()` los revela y resetea el contador de duración a 0:00.
- `dictation_daemon.py`: `_hud.show()` movido al main thread **antes** del spawn del thread de captura (línea 1764). Dentro del thread, post `stream.start()` se llama `_hud.mark_recording_started()`. Branches de error y early-cancel ahora ejecutan `_hud.hide()`.

**Resultado:** double-tap muestra HUD instantáneo (sin REC). Cuando el mic captura de verdad, aparece REC + dot pulsante y arranca el contador. Si el stream falla / timeout, el HUD se retira solo.

**Commit:** N/A (no es repo git).

**Lección operacional separada:** validar siempre que el bundle instalado en `/Applications/AutoWhisper.app` contenga los edits. El primer intento de validación falló porque el usuario estaba corriendo el `.app` compilado el 15 May — los cambios en `~/src/auto-whisper-v5/` no estaban en el bundle. Flow correcto post-edit: `bash scripts/build_app.sh` → `cp -R dist/AutoWhisper.app /Applications/` → `launchctl bootout/bootstrap` → `tccutil reset Accessibility/Microphone com.auto-whisper.app` (el re-codesign ad-hoc cambia identidad TCC y revoca permisos).

**Relacionado:** [memory: AirPods accuracy bug](feedback_py2app_sounddevice_dlopen.md), [memory: py2app sounddevice dlopen trap].

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
