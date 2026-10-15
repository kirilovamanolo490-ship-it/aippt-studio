-- Operates only on Work-local staging copies supplied by Studio.
-- Never quits PowerPoint or closes unrelated presentations.
on run argv
    set candidateFile to POSIX file (item 1 of argv)
    set referencePath to item 2 of argv
    set phase to "initialize"
    set ownedDeck to missing value
    try
        -- Bring PowerPoint to the foreground before asking it to open a
        -- sandboxed staging deck.  A just-launched background instance can
        -- otherwise return kBusyErr (-9074) before it has a document context.
        tell application "Microsoft PowerPoint" to activate
        delay 1
        -- Save/reopen can exceed a minute on a cold Microsoft 365 launch. The
        -- Python caller has a slightly larger wall-clock guard and receives
        -- this script's staged failure rather than a generic subprocess kill.
        with timeout of 180 seconds
            tell application "Microsoft PowerPoint"
                if referencePath is not "" then
                    set phase to "reference_open"
                    open (POSIX file referencePath)
                    set ownedDeck to my findOwnedPresentation(item 4 of argv)
                    close ownedDeck saving no
                    set ownedDeck to missing value
                end if
                set phase to "candidate_open"
                open candidateFile
                set ownedDeck to my findOwnedPresentation(item 3 of argv)
                set slideCount to count of slides of ownedDeck
                set phase to "candidate_save"
                save ownedDeck
                set phase to "candidate_close"
                close ownedDeck saving no
                set ownedDeck to missing value
                set phase to "candidate_reopen"
                open candidateFile
                set ownedDeck to my findOwnedPresentation(item 3 of argv)
                set reopenedCount to count of slides of ownedDeck
                close ownedDeck saving no
                set ownedDeck to missing value
                if reopenedCount is not slideCount then error "Slide count changed after roundtrip"
                return "{\"status\":\"passed\",\"phase\":\"complete\",\"slide_count\":" & slideCount & "}"
            end tell
        end timeout
    on error errorMessage number errorNumber
        if ownedDeck is not missing value then
            try
                tell application "Microsoft PowerPoint" to close ownedDeck saving no
            end try
        end if
        error "AIPPT_STAGE=" & phase & ": " & errorMessage number errorNumber
    end try
end run


on findOwnedPresentation(token)
    tell application "Microsoft PowerPoint"
        repeat with i from 1 to count of presentations
            set candidateDeck to presentation i
            if (name of candidateDeck as text) contains token then return candidateDeck
        end repeat
    end tell
    error "The opened staging presentation could not be identified"
end findOwnedPresentation
