#!/usr/bin/env python3
"""
macOS notification display.

Extracted from text_processor.py in Phase 0 refactor — notifications are a
side-effectful concern, not text processing. Kept in auto_whisper/ for now;
may migrate to a dedicated UI module in a later phase.
"""

import logging
import re
import subprocess

logger = logging.getLogger(__name__)


def notify(title: str, message: str) -> None:
    """Show macOS notification via osascript."""
    try:
        clean = re.sub(r'[*_#`\[\]()]', '', message)
        clean = clean.replace('"', "'").replace("\\", "")[:200]
        script = f'display notification "{clean}" with title "{title}"'
        subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
    except Exception as e:
        logger.warning(f"Notification failed: {e}")
