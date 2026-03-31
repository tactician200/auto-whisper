on run
	display dialog "Drop meeting recordings onto this app to import them into MeetingsIntel." buttons {"OK"} default button "OK" with title "Send to MeetingsIntel"
end run

on open droppedItems
	set importerPath to "/Users/mantra/src/auto-whisper/send_to_meetings_intel.sh"
	set shellCommand to "bash " & quoted form of importerPath

	repeat with anItem in droppedItems
		set shellCommand to shellCommand & space & quoted form of POSIX path of anItem
	end repeat

	try
		do shell script shellCommand
		display notification "Meeting file sent to MeetingsIntel" with title "Send to MeetingsIntel"
	on error errMsg
		display dialog errMsg buttons {"OK"} default button "OK" with title "Send to MeetingsIntel"
	end try
end open
