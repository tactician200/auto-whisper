#!/usr/bin/env python3
"""
CLI: text-to-speech tool.

Usage:
    python speak.py "Hola mundo"
    echo "Texto largo" | python speak.py
    python speak.py --file resumen.md
    python speak.py --file resumen.md --split      # voice + display
    python speak.py --backend edge "Prueba"        # force backend
    python speak.py --backend macos "Offline test"
"""

import sys
from pathlib import Path
from voice_agent import speak, DEFAULT_BACKEND


def main():
    args = sys.argv[1:]

    # Parse flags
    backend = DEFAULT_BACKEND
    split_mode = False
    file_path = None
    text_args = []

    i = 0
    while i < len(args):
        if args[i] == "--backend" and i + 1 < len(args):
            backend = args[i + 1]
            i += 2
        elif args[i] == "--file" and i + 1 < len(args):
            file_path = args[i + 1]
            i += 2
        elif args[i] == "--split":
            split_mode = True
            i += 1
        elif args[i].startswith("--"):
            i += 1
        else:
            text_args.append(args[i])
            i += 1

    # Get text
    if file_path:
        text = Path(file_path).read_text(encoding="utf-8")
    elif text_args:
        text = " ".join(text_args)
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        print("Usage: python speak.py [--backend google|edge|macos] [--split] [--file path] [text]")
        sys.exit(1)

    if not text.strip():
        print("No text to speak")
        sys.exit(1)

    if split_mode:
        from split_output import split_response
        voice_text, display_text = split_response(text)
        print(display_text)
        print("\n--- Speaking ---")
        speak(voice_text, backend=backend)
    else:
        speak(text, backend=backend)


if __name__ == "__main__":
    main()
