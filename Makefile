# auto-whisper v5 — developer commands
#
# Usage: `make <target>`. Run `make` or `make help` to see this menu.
#
# Conventions:
# - PYTHON points at the v5 venv. If you've activated the venv shell-side,
#   override with `PYTHON=python make test`.
# - Targets that interact with the v4.2 LaunchAgent (unload-v4 / load-v4)
#   use `launchctl unload`/`load` — NOT `kill` — so launchd KeepAlive
#   doesn't revive the daemon mid-test (Phase 0 lesson learned).

PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
LAUNCHAGENT_PLIST := $(HOME)/Library/LaunchAgents/com.auto-whisper.plist

.DEFAULT_GOAL := help
.PHONY: help test test-verbose test-quick run-v5 run-service stop-service unload-v4 load-v4 v4-status clean install-v5 revert-to-v4 v5-status

help:  ## Show this help menu
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  %-18s %s\n", $$1, $$2}'

test:  ## Run full test suite quietly
	PYTHONPATH=. $(PYTEST) tests/ -q

test-verbose:  ## Run full test suite with per-test names
	PYTHONPATH=. $(PYTEST) tests/ -v

test-quick:  ## Run tests, stop on first failure (fast feedback during dev)
	PYTHONPATH=. $(PYTEST) tests/ -x --ff -q

run-v5:  ## Launch v5 menubar app, direct Groq path (foreground)
	PYTHONPATH=. $(PYTHON) auto_whisper/main.py

run-v5-via-service:  ## Launch v5 menubar with AUTO_WHISPER_USE_SERVICE=1 (requires `make run-service` separately)
	AUTO_WHISPER_USE_SERVICE=1 PYTHONPATH=. $(PYTHON) auto_whisper/main.py

run-v5-via-service-full:  ## Launch v5 with BOTH transcription AND processing routed through service
	AUTO_WHISPER_USE_SERVICE=1 AUTO_WHISPER_USE_SERVICE_PROCESSING=1 \
		PYTHONPATH=. $(PYTHON) auto_whisper/main.py

run-v5-via-service-all:  ## Launch v5 with transcription + processing + TTS all routed through service
	AUTO_WHISPER_USE_SERVICE=1 AUTO_WHISPER_USE_SERVICE_PROCESSING=1 \
		AUTO_WHISPER_USE_SERVICE_TTS=1 \
		PYTHONPATH=. $(PYTHON) auto_whisper/main.py

run-v5-beta:  ## Launch v5 in beta mode — all flags ON, daemon auto-spawns service. Single command.
	AUTO_WHISPER_USE_SERVICE=1 AUTO_WHISPER_USE_SERVICE_PROCESSING=1 \
		AUTO_WHISPER_USE_SERVICE_TTS=1 AUTO_WHISPER_AUTOSTART_SERVICE=1 \
		PYTHONPATH=. $(PYTHON) auto_whisper/main.py

run-service:  ## Launch v5 service (foreground; Ctrl+C to stop)
	PYTHONPATH=. $(PYTHON) -m auto_whisper_service.main

stop-service:  ## Kill any running auto-whisper-service process
	@pkill -f "auto_whisper_service.main" 2>/dev/null && echo "service stopped" || echo "service not running"

unload-v4:  ## Unload v4.2 LaunchAgent (correct way — survives KeepAlive)
	@if [ -f "$(LAUNCHAGENT_PLIST)" ]; then \
		launchctl unload "$(LAUNCHAGENT_PLIST)" && echo "v4.2 unloaded"; \
	else \
		echo "no LaunchAgent plist at $(LAUNCHAGENT_PLIST)"; \
	fi

load-v4:  ## Re-load v4.2 LaunchAgent (after unload-v4)
	@if [ -f "$(LAUNCHAGENT_PLIST)" ]; then \
		launchctl load "$(LAUNCHAGENT_PLIST)" && echo "v4.2 loaded"; \
	else \
		echo "no LaunchAgent plist at $(LAUNCHAGENT_PLIST)"; \
	fi

v4-status:  ## Show v4.2 LaunchAgent state
	@launchctl list | grep com.auto-whisper || echo "v4.2 not running"

install-v5:  ## Switch active LaunchAgent to v5 (unloads v4.2; reversible with revert-to-v4)
	@bash scripts/install-v5.sh

revert-to-v4:  ## Switch active LaunchAgent back to v4.2 (preserves v5 plist)
	@bash scripts/revert-to-v4.sh

v5-status:  ## Show v5 LaunchAgent state
	@launchctl list | grep com.auto-whisper.v5 || echo "v5 not loaded"

clean:  ## Remove caches (pytest, mypy, ruff, __pycache__)
	@find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
	@echo "caches cleaned"
