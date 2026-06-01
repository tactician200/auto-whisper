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
    # All runtime deps from `pip list` go here. py2app's autodetection misses
    # too many transitive deps for the FastAPI+uvicorn+pydantic v2 stack to
    # rely on (annotated_doc, click, h11, starlette, etc. — each one a
    # separate rebuild). Listing everything explicitly is verbose but
    # rebuilds are deterministic. Excluded: pytest, py2app, build tools.
    'packages': [
        # First-party
        'auto_whisper',
        'auto_whisper_service',
        'shared',
        # Native binaries (must be in `packages`, not `includes`)
        'sounddevice',
        'numpy',
        # macOS bindings
        'rumps',
        'objc',
        'AppKit',
        'Foundation',
        'Quartz',
        # HTTP / async / FastAPI stack
        'fastapi',
        'starlette',
        'uvicorn',
        'h11',
        'httpcore',
        'httpx',
        'httptools',
        'anyio',
        'sniffio',
        'click',
        # pydantic v2
        'pydantic',
        'pydantic_core',
        'annotated_doc',
        'annotated_types',
        'typing_inspection',
        'typing_extensions',       # widely needed transitive; safer in packages
        # LLM / cloud
        'groq',
        'edge_tts',
        # google.cloud / google-genai NOT included — only used by
        # voice_agent._speak_google() which is opt-in (lazy import inside
        # a function). py2app can't resolve the `google` namespace
        # cleanly anyway. If we ever want Google Cloud TTS in the bundle,
        # add `google.cloud.texttospeech` as a package and ship the auth
        # deps separately.
        # Network / TLS
        'cryptography',
        'cffi',
        'pycparser',
        # aiohttp family (used by edge_tts + google_genai)
        'aiohttp',
        'aiosignal',
        'aiohappyeyeballs',
        'attrs',
        'frozenlist',
        'multidict',
        'propcache',
        'yarl',
        # Misc deps that show up in the chain
        'multipart',               # python-multipart (legacy import name)
        'python_multipart',        # python-multipart (modern import name; both needed)
        'dotenv',                  # python-dotenv
        'tenacity',
        'requests',
        'urllib3',
        'pyasn1',
        'pyasn1_modules',
        'yaml',                    # PyYAML
        'pygments',
    ],
    'includes': [
        # Pure-python single-file deps fit better as includes (lighter).
        # NOTE: includes go into python311.zip; if a module has any C
        # extension or needs __path__ resolution at runtime, move it to
        # `packages` instead.
        'idna',
        'certifi',
        'charset_normalizer',
        'distro',
        'tabulate',
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
