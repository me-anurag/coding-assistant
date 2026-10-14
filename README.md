# Paste Helper (Python only)

Copies and "pastes" on sites that block Ctrl+C / Ctrl+V, with no browser extension.

## Install (Windows)
    pip install -r requirements.txt
    python paste_helper.py
(or double-click run_windows.bat)

## Keys
- F8  - copy the selected text (Ctrl+C, then Ctrl+Insert if the site blocked Ctrl+C)
- F9  - type the clipboard into the focused box
- F10 - stop

You can also copy any normal way (Ctrl+C, right-click, from Word...) and just press F9.

## Formatting
Typing cannot carry formatting by itself, so the helper reads the formatted (HTML) clipboard and
reproduces it with keyboard shortcuts in rich-text editors:
- bold, italic, underline (Ctrl+B / Ctrl+I / Ctrl+U)
- bullets, numbered lists, headings (bold) and line breaks

Not reproducible this way: colours, fonts, sizes, links, tables, images.
Turn "Keep formatting" OFF for plain text boxes (in Firefox, Ctrl+B opens the bookmarks sidebar there).

## Limits
- If a site blocks the copy action itself (not just the shortcut), nothing can be copied from it
  without a browser extension.
- Works only if the target editor supports Ctrl+B/I/U.
- Windows: if the target app runs as admin, run Paste Helper as admin too.
