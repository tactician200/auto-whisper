#!/usr/bin/env bash
# Post-build patches for dist/AutoWhisper.app.
#
# Why: py2app puts single-file modules like `sounddevice` into the python
# zip (Resources/lib/python311.zip). When sounddevice runs ctypes.CDLL on
# libportaudio.dylib using its own __file__-relative path, dlopen fails
# because it can't read shared libraries from inside a zip archive.
#
# Fix: extract sounddevice (and its sibling _sounddevice_data/ which has
# the actual .dylib) from the venv onto the bundle's filesystem
# site-packages, where ctypes can resolve the absolute path.
#
# Idempotent — re-running just overwrites the files.

set -euo pipefail

APP="dist/AutoWhisper.app"
BUNDLE_LIB="$APP/Contents/Resources/lib/python3.11"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_SITE="$REPO_ROOT/.venv/lib/python3.11/site-packages"

if [ ! -d "$APP" ]; then
  echo "ERROR: $APP not found — run py2app first."
  exit 1
fi

if [ ! -d "$VENV_SITE" ]; then
  echo "ERROR: venv site-packages not found at $VENV_SITE"
  exit 1
fi

mkdir -p "$BUNDLE_LIB"

# --- sounddevice + _sounddevice_data ---
echo "  ✓ sounddevice + _sounddevice_data → $BUNDLE_LIB/"
# Copy the .py source (so __file__ resolves to a real filesystem path).
cp "$VENV_SITE/sounddevice.py" "$BUNDLE_LIB/sounddevice.py"
cp "$VENV_SITE/_sounddevice.py" "$BUNDLE_LIB/_sounddevice.py" 2>/dev/null || true
# Copy the data dir containing the actual libportaudio.dylib.
rm -rf "$BUNDLE_LIB/_sounddevice_data"
cp -R "$VENV_SITE/_sounddevice_data" "$BUNDLE_LIB/_sounddevice_data"
# Strip extended attributes & quarantine bits Apple adds.
xattr -cr "$BUNDLE_LIB/_sounddevice_data" 2>/dev/null || true

# --- remove sounddevice from python311.zip so the bundle's site-packages
#     copy wins on sys.path. py2app puts the zip ahead of the filesystem
#     dir, so leaving sounddevice.pyc in the zip would shadow our copy
#     and re-trigger the dlopen-from-zip failure. ---
ZIP="$APP/Contents/Resources/lib/python311.zip"
echo "  ✓ remove sounddevice from $ZIP"
# `zip -d` removes entries in-place. Silent if entry doesn't exist (e.g.
# the file already had been stripped on a previous run).
zip -d "$ZIP" \
  "sounddevice.pyc" \
  "_sounddevice.pyc" \
  "sounddevice-*/*" \
  "sounddevice-*" \
  "_sounddevice_data/*" \
  "_sounddevice_data/" \
  2>/dev/null || true

# --- verify the .dylib is actually present and readable ---
DYLIB="$BUNDLE_LIB/_sounddevice_data/portaudio-binaries/libportaudio.dylib"
if [ ! -f "$DYLIB" ]; then
  echo "ERROR: libportaudio.dylib missing at $DYLIB after copy"
  exit 1
fi
echo "  ✓ libportaudio.dylib present ($(stat -f '%z' "$DYLIB") bytes)"

# --- copy .env to the bundle so the daemon can find API keys ---
# shared/config.py reads `.env` from Path(__file__).parent.parent, which
# inside the bundle resolves to Resources/lib/python3.11/. Without this
# copy, the daemon boots with no Groq key and silently falls back to local
# whisper.cpp (or just refuses to work in cloud mode).
#
# Future refactor: have config.py look in ~/Library/Application Support/
# auto-whisper/.env so the bundle stays portable and secrets live in
# user-land. Until then, this copy lets the existing flow work.
ENV_SRC="$REPO_ROOT/.env"
ENV_DST="$BUNDLE_LIB/.env"
if [ -f "$ENV_SRC" ]; then
  echo "  ✓ .env → $ENV_DST"
  cp "$ENV_SRC" "$ENV_DST"
  chmod 600 "$ENV_DST"
else
  echo "  ! .env not found at $ENV_SRC — bundle will run in local-only mode"
fi

# --- re-sign (the bundle was signed before our copy; codesign needs to
#     re-validate after we mutated Resources/). ---
echo "  ✓ re-codesign ad-hoc"
codesign --force --deep --sign - "$APP" 2>&1 | tail -2

echo
echo "post-build patches applied to $APP"
