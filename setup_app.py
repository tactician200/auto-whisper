"""py2app build script for auto-whisper v5.

Build:
    .venv/bin/python setup_app.py py2app
    codesign --force --deep --sign - dist/AutoWhisper.app

Install:
    rm -rf /Applications/AutoWhisper.app
    cp -R dist/AutoWhisper.app /Applications/

The .app is LSUIElement (menubar only, no Dock icon), ad-hoc-signable, and
ready to be loaded by the LaunchAgent at `com.auto-whisper.v5.plist` once
that plist points at /Applications/AutoWhisper.app/Contents/MacOS/AutoWhisper.

Icon: assets/AppIcon.icns — regenerate via scripts/render_app_icon.py.

This file is committed (not gitignored) so users who clone can package
their own .app. No personal paths inside.
"""

from setuptools import setup

APP = ['auto_whisper/main.py']

OPTIONS = {
    'argv_emulation': False,
    'iconfile': 'assets/AppIcon.icns',
    'plist': {
        'CFBundleName': 'AutoWhisper',
        'CFBundleDisplayName': 'AutoWhisper',
        'CFBundleIdentifier': 'com.auto-whisper.app',
        'CFBundleVersion': '5.0',
        'CFBundleShortVersionString': '5.0',
        'CFBundleIconFile': 'AppIcon',          # without extension; py2app handles it
        'CFBundleExecutable': 'AutoWhisper',
        'LSUIElement': True,                    # menubar app, no Dock icon
        'LSMinimumSystemVersion': '13.0',
        'NSHumanReadableCopyright': '© 2026 auto-whisper contributors · MIT',
        'NSMicrophoneUsageDescription': (
            'AutoWhisper needs microphone access to record dictation.'
        ),
        'NSAppleEventsUsageDescription': (
            'AutoWhisper sends keyboard events to paste transcribed text.'
        ),
        'NSHighResolutionCapable': True,
        # Helps macOS show "AutoWhisper" not "Python" in Activity Monitor /
        # Force Quit. (CFBundleName above is the primary source; this is the
        # belt-and-suspenders for older AppKit dialogs.)
        'CFBundleGetInfoString': 'AutoWhisper 5.0 — macOS dictation',
    },
    # IMPORTANT: any package with a native binary (.dylib / .so) MUST be in
    # `packages`, not `includes`. py2app puts `includes` into a zipped
    # site-packages, and dlopen can't load shared libraries from inside a zip.
    # sounddevice ships libportaudio.dylib, numpy has C extensions, rumps
    # bridges AppKit through PyObjC, etc. — all go in `packages`.
    'packages': [
        'auto_whisper',
        'auto_whisper_service',
        'shared',
        'sounddevice',
        'numpy',
        'rumps',
        'groq',
        'httpx',
        'fastapi',
        'uvicorn',
        'pydantic',
        'pydantic_core',
        'edge_tts',
    ],
    'includes': [
        # pure-python deps that don't ship binaries can stay here (smaller
        # bundle than putting them in `packages`). Add only if py2app misses
        # them on autodetection.
    ],
    'excludes': [
        'meetings_intel',
        'tests',
        'pytest',
        'py2app',
    ],
}

setup(
    app=APP,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
