# Get Meaning

**Instant word definitions, anywhere on your computer.**

Select a word in *any* application — your browser, a desktop app like Claude,
Microsoft Word, a PDF, a code editor — press a hotkey, and a small popup shows
you what it means. No copy-pasting into a search bar, no switching windows.

Because it runs as a lightweight background utility (not a browser extension),
it works **everywhere**, not just inside Chrome.

<!-- Add a screenshot or GIF here once you have one, e.g.:
![demo](docs/demo.gif)
-->

---

## Features

- 🔎 **Look up any selected word** with a single hotkey (default: `Alt+Shift+D`)
- 🖥️ **Works in every app**, not just the browser — desktop apps, PDFs, editors
- 🌍 **Cross-platform** — Windows, macOS, and Linux (X11)
- 🪶 **Tiny and dependency-light** — three small Python libraries
- 🔒 **Respects your clipboard** — restores whatever you had copied afterwards
- ⚙️ **Configurable** — change the hotkey, language, and popup timeout

Definitions come from the free [Dictionary API](https://dictionaryapi.dev/)
(no account or API key required).

---

## Requirements

- **Python 3.7+**
- An internet connection (definitions are fetched online)

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/<your-username>/get-meaning.git
cd get-meaning

# 2. (Recommended) create a virtual environment
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Usage

Start it:

```bash
python get_meaning.py
```

Then, in **any** application: **select a word → press `Alt+Shift+D`**.
A popup appears next to your cursor. Press `Esc` or click it to dismiss.

Quit by closing the terminal window or pressing `Ctrl+C` in it.

### Convenience launchers

- **Windows:** double-click `run_windows.bat`
- **macOS / Linux:** `chmod +x run_unix.sh && ./run_unix.sh`

### Options

| Flag         | Default          | Description                                  |
|--------------|------------------|----------------------------------------------|
| `--hotkey`   | `<alt>+<shift>+d` | The trigger combo. Both `alt+shift+d` and `<alt>+<shift>+d` styles work. |
| `--lang`     | `en`             | Dictionary language code (e.g. `en`, `es`, `fr`, `de`). |
| `--timeout`  | `12`             | Seconds before the popup auto-closes.        |

Example:

```bash
python get_meaning.py --hotkey "ctrl+alt+d" --lang en --timeout 15
```

---

## Platform-specific setup

Capturing a global hotkey and reading the selection requires OS permissions.
This is the most common reason a fresh install "does nothing" — please check
the note for your system.

### Windows
Usually works out of the box. If the hotkey doesn't respond, run the terminal
(or `run_windows.bat`) **as Administrator**, since some apps run elevated and
a non-elevated listener can't see keystrokes sent to them.

### macOS
Grant your terminal (or Python) permission under
**System Settings → Privacy & Security**:
- **Accessibility** — required to detect the hotkey and simulate copy
- **Input Monitoring** — required to read the keyboard

You'll be prompted the first time; if you miss it, add the app manually.
The copy shortcut used on macOS is `⌘+C` (handled automatically).

### Linux
- Works under **X11**. **Wayland** is not reliably supported by the underlying
  input library — if you're on Wayland, an X11 session is the workaround.
- Install a clipboard backend if you don't have one:
  ```bash
  sudo apt install xclip      # or: sudo apt install xsel
  ```
- Global input access may require running from a session with the right
  permissions (or, as a last resort, with `sudo`).

---

## How it works

```
Select a word
      │
      ▼
Press the hotkey ──► app simulates Copy (Ctrl/⌘+C)
      │
      ▼
Read the clipboard ──► clean it down to a single word
      │
      ▼
Fetch the definition from the free Dictionary API
      │
      ▼
Show a popup next to the cursor  (clipboard is then restored)
```

To capture the selection, the app briefly places a marker on the clipboard,
simulates a copy, and checks whether the clipboard changed — that's how it
reliably tells "you selected a word" from "you selected nothing" without ever
showing you a stale result. Your previous clipboard contents are restored a
moment later.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Hotkey does nothing | Check the platform setup above; on Windows try running as Administrator. |
| "No word selected" every time | Make sure the word is actually highlighted before pressing the hotkey. |
| "Clipboard unavailable" (Linux) | Install `xclip` or `xsel`. |
| "Couldn't reach the dictionary" | Check your internet connection. |
| Popup shows the wrong/previous word | Re-select the word; give the app a moment between lookups. |

---

## Roadmap

- [ ] Optional **double-click** trigger (in addition to the hotkey)
- [ ] **Offline dictionary** mode (bundle WordNet — works with no internet)
- [ ] **System-tray icon** with start/quit and settings
- [ ] **Start on login** option
- [ ] Prebuilt **standalone executables** (no Python needed) via PyInstaller

---

## Contributing

Issues and pull requests are welcome. If you hit a platform quirk, please open
an issue with your OS, Python version, and what happened.

---

## License

[MIT](LICENSE) © 2026 Prayag Padwal
