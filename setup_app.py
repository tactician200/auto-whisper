"""py2app build script for auto-whisper v5.

Build:    .venv/bin/python setup_app.py py2app
Result:   dist/AutoWhisper.app

Ad-hoc sign after build:
    codesign --force --deep --sign - dist/AutoWhisper.app

This file is intentionally committed (kept off .gitignore) so users who
clone the repo can package their own .app. No personal paths inside.
"""

from setuptools import setup

APP = ['auto_whisper/main.py']

OPTIONS = {
    'argv_emulation': False,
    'plist': {
        'CFBundleName': 'AutoWhisper',
        'CFBundleDisplayName': 'AutoWhisper',
        'CFBundleIdentifier': 'com.auto-whisper.app',
        'CFBundleVersion': '5.0',
        'CFBundleShortVersionString': '5.0',
        'LSUIElement': True,
        'NSMicrophoneUsageDescription': (
            'AutoWhisper needs microphone access to record dictation.'
        ),
        'NSAppleEventsUsageDescription': (
            'AutoWhisper sends keyboard events to paste transcribed text.'
        ),
    },
    'packages': [
        'auto_whisper',
        'auto_whisper_service',
        'shared',
    ],
    'includes': [
        'rumps',
        'groq',
        'numpy',
        'sounddevice',
        'httpx',
        'fastapi',
        'uvicorn',
        'pydantic',
        'edge_tts',
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
