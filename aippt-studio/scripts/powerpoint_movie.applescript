-- Only opens/closes the uniquely named Studio staging presentation.
on run argv
 set ownedDeck to missing value
 set phase to "movie_open"
 try
  with timeout of 1200 seconds
   tell application "Microsoft PowerPoint"
    open (POSIX file (item 1 of argv))
    repeat with i from 1 to count of presentations
     set doc to presentation i
     if (name of doc as text) contains (item 3 of argv) then set ownedDeck to doc
    end repeat
    if ownedDeck is missing value then error "Cannot identify Studio staging presentation"
    set phase to "movie_export"
    save ownedDeck in (POSIX file (item 2 of argv)) as save as movie
   end tell
    -- Some Office builds return before the exporter flushes its final file.
    set stableSamples to 0
    set lastSize to -1
    repeat 120 times
     delay 1
     try
      set currentSize to (do shell script ("/usr/bin/stat -f %z " & quoted form of (item 2 of argv))) as integer
      if currentSize > 100 and currentSize = lastSize then
       set stableSamples to stableSamples + 1
      else
       set stableSamples to 0
      end if
      set lastSize to currentSize
      if stableSamples >= 3 then exit repeat
     end try
    end repeat
    if stableSamples < 3 then error "Movie output did not become stable"
    tell application "Microsoft PowerPoint" to close ownedDeck saving no
    set ownedDeck to missing value
    return "{\"status\":\"passed\",\"backend\":\"mac-powerpoint-movie\"}"
  end timeout
 on error msg number num
  if ownedDeck is not missing value then
   try
    tell application "Microsoft PowerPoint" to close ownedDeck saving no
   end try
  end if
  error "AIPPT_STAGE=" & phase & ": " & msg number num
 end try
end run
