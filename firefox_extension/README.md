# Copy & Paste Unblocker (Firefox extension)

| Key | Action |
|-----|--------|
| F8  | Copy the selected text, keeping its formatting (works even if Ctrl+C is blocked) |
| F9  | Paste the last F8 copy into the focused box, keeping formatting where the box supports it |
| F7  | Force text selection on/off (for pages that stop you selecting text) |

## Install (temporary, for testing)
1. In Firefox open  about:debugging#/runtime/this-firefox
2. Click "Load Temporary Add-on..."
3. Select the file  firefox_extension\manifest.json
4. Reload any already open tabs. (Temporary add-ons are removed when Firefox closes; just load it again.)

## Notes
- Formatting only survives in rich-text boxes (contenteditable editors). Plain <input>/<textarea> boxes
  can only hold plain text, so they get plain text with line breaks.
- F8 also puts the HTML + text on your system clipboard so Ctrl+V works in Word, Gmail, Notepad, etc.
- Do not run the Python Paste Helper at the same time, or F9 will paste twice.
- The extension does not work on Firefox's own pages (about:*, addons.mozilla.org).
