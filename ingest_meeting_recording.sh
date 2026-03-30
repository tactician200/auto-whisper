#!/usr/bin/env bash
set -euo pipefail

INBOX="${HOME}/MeetingInbox"
MEETING_AGENT="gui/$(id -u)/com.meetingtranscriber"

usage() {
    cat <<EOF
Usage: $(basename "$0") <audio-file> [audio-file...]

Copies meeting recordings into ~/MeetingInbox so MeetingsIntel can transcribe
and analyze them automatically.
EOF
}

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

copy_one() {
    local src="$1"
    local base stem ext ts dest

    if [[ ! -f "$src" ]]; then
        log "Skipping non-file: $src"
        return 1
    fi

    base="$(basename "$src")"
    stem="${base%.*}"
    ext=""
    if [[ "$base" == *.* ]]; then
        ext=".${base##*.}"
    fi

    ts="$(date '+%Y-%m-%d_%H-%M-%S')"
    dest="${INBOX}/${ts}_${stem}${ext}"
    cp "$src" "$dest"
    log "Imported: $dest"
}

main() {
    if [[ "$#" -eq 0 ]]; then
        usage
        exit 1
    fi

    mkdir -p "$INBOX"

    for src in "$@"; do
        copy_one "$src"
    done

    launchctl kickstart -k "$MEETING_AGENT" >/dev/null 2>&1 || true
    log "Meeting transcriber notified"
}

main "$@"
