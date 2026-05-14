#!/usr/bin/env python3

# Apply bundle branding BEFORE importing dictation_daemon — rumps/AppKit
# read CFBundleName as soon as they touch the running process, and once
# read those values are cached. Branding has to land first or all dialogs
# show "Python".
from auto_whisper import app_branding

app_branding.apply()

from auto_whisper.dictation_daemon import main


if __name__ == "__main__":
    main()
