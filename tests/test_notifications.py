"""
Tests for auto_whisper.notifications.notify().

Verify message sanitization (markdown stripped, quotes escaped, length capped)
and graceful failure when osascript is unavailable or errors.
"""

from unittest.mock import patch

from auto_whisper.notifications import notify


def test_notify_invokes_osascript():
    with patch("auto_whisper.notifications.subprocess.run") as mock_run:
        notify("Title", "hello")
        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        assert args[0] == "/usr/bin/osascript"
        assert "-e" in args


def test_notify_strips_markdown_characters():
    with patch("auto_whisper.notifications.subprocess.run") as mock_run:
        notify("Title", "**bold** _italic_ #heading `code` [link](url)")
        script = mock_run.call_args.args[0][-1]
        for ch in ("*", "_", "#", "`", "[", "]", "(", ")"):
            assert ch not in script.split('"')[1], f"{ch!r} should be stripped"


def test_notify_truncates_long_messages():
    long_msg = "x" * 500
    with patch("auto_whisper.notifications.subprocess.run") as mock_run:
        notify("Title", long_msg)
        script = mock_run.call_args.args[0][-1]
        msg_inside_quotes = script.split('"')[1]
        assert len(msg_inside_quotes) <= 200


def test_notify_escapes_double_quotes():
    with patch("auto_whisper.notifications.subprocess.run") as mock_run:
        notify("Title", 'he said "hi"')
        script = mock_run.call_args.args[0][-1]
        # double quotes inside message are replaced with single quotes so
        # the outer AppleScript string doesn't break
        inner = script.split('"')[1]
        assert '"' not in inner
        assert "'" in inner


def test_notify_swallows_subprocess_errors():
    with patch("auto_whisper.notifications.subprocess.run", side_effect=OSError("boom")):
        # Must not raise — notify should degrade silently, not crash callers
        notify("Title", "message")
