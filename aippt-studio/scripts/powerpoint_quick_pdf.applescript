-- Export only the uniquely named temporary copy supplied by Studio.
on run argv
    set deckFile to POSIX file (item 1 of argv)
    set pdfFile to POSIX file (item 2 of argv)
    set token to item 3 of argv
    set ownedDeck to missing value
    try
        with timeout of 300 seconds
            tell application "Microsoft PowerPoint"
                open deckFile
                set ownedDeck to my findOwnedPresentation(token)
                save ownedDeck in pdfFile as save as PDF
                close ownedDeck saving no
                set ownedDeck to missing value
            end tell
        end timeout
    on error errorMessage number errorNumber
        if ownedDeck is not missing value then
            try
                tell application "Microsoft PowerPoint" to close ownedDeck saving no
            end try
        end if
        error errorMessage number errorNumber
    end try
end run

on findOwnedPresentation(token)
    tell application "Microsoft PowerPoint"
        repeat with i from 1 to count of presentations
            set candidateDeck to presentation i
            if (name of candidateDeck as text) contains token then return candidateDeck
        end repeat
    end tell
    error "The opened temporary presentation could not be identified"
end findOwnedPresentation
