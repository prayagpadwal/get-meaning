#!/usr/bin/env python3
"""
Get Meaning — a tiny, cross-platform background utility that shows the
definition of the currently selected word anywhere on your computer
(your browser, the Claude app, Word, a PDF, a code editor, anything).

How it works:
  1. Select a word in any application.
  2. Press the hotkey (default: Ctrl+Shift+D).
  3. Get Meaning copies the selection, looks it up in a free online
     dictionary, and shows a small popup next to your cursor.

Works on Windows, macOS, and Linux (X11). See the README for the
per-platform permissions each operating system requires.

Usage:
    python get_meaning.py
    python get_meaning.py --hotkey "<ctrl>+<alt>+d" --lang en --timeout 15
"""

import sys
import time
import queue
import argparse
import threading

MIN_PYTHON = (3, 7)
if sys.version_info < MIN_PYTHON:
    sys.exit(f"Get Meaning needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ "
             f"(you have {sys.version.split()[0]}).")

# --- Third-party imports, with a friendly message if they're missing --------
_MISSING = []
try:
    import requests
except ImportError:
    _MISSING.append("requests")
try:
    import pyperclip
except ImportError:
    _MISSING.append("pyperclip")
try:
    from pynput import keyboard
    from pynput.keyboard import Controller, Key
except ImportError:
    _MISSING.append("pynput")

if _MISSING:
    sys.exit(
        "Missing required libraries: " + ", ".join(_MISSING) + "\n"
        "Install them with:\n"
        "    pip install -r requirements.txt\n"
        "or\n"
        "    pip install " + " ".join(_MISSING)
    )

import tkinter as tk  # noqa: E402  (stdlib, always available)

# ---------------------------------------------------------------------------
# Defaults (all overridable via command-line flags — see main())
# ---------------------------------------------------------------------------
DEFAULT_HOTKEY = "<ctrl>+<shift>+d"
DEFAULT_LANG = "en"
DEFAULT_TIMEOUT_S = 12
API = "https://api.dictionaryapi.dev/api/v2/entries/{lang}/{word}"
MAX_PARTS_OF_SPEECH = 3
MAX_DEFS_PER_PART = 2

IS_MAC = sys.platform == "darwin"
COPY_MODIFIER = Key.cmd if IS_MAC else Key.ctrl

# A value we never expect to see selected — lets us reliably detect the case
# where the copy did nothing (e.g. no word was selected).
_SENTINEL = "\x00__get_meaning_no_selection__\x00"

# Results flow from the hotkey thread -> the main (UI) thread via this queue.
_result_q: "queue.Queue[str]" = queue.Queue()
_kbctl = Controller()


# ---------------------------------------------------------------------------
# Grabbing the selected word
# ---------------------------------------------------------------------------
def _send_copy():
    """Simulate the platform's copy shortcut, releasing any hotkey modifiers
    that might interfere first."""
    time.sleep(0.05)
    # The user may still be holding the hotkey (e.g. Ctrl+Shift+D). Release the
    # extra modifiers so they don't turn our copy into a different shortcut.
    for k in (Key.shift, Key.alt):
        try:
            _kbctl.release(k)
        except Exception:
            pass
    try:
        _kbctl.press(COPY_MODIFIER)
        _kbctl.press("c")
        _kbctl.release("c")
        _kbctl.release(COPY_MODIFIER)
    except Exception:
        pass


def get_selected_text():
    """Copy the current selection and return it (or ""), without permanently
    clobbering whatever the user already had on their clipboard."""
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = ""

    # Prime the clipboard with a sentinel so we can tell whether the copy
    # actually captured anything.
    try:
        pyperclip.copy(_SENTINEL)
    except Exception:
        # Clipboard backend unavailable (common on Linux without xclip/xsel).
        return None  # signals a clipboard error to the caller

    _send_copy()

    # Poll briefly for the clipboard to change — slower machines need this.
    selected = _SENTINEL
    for _ in range(16):  # up to ~0.8s
        time.sleep(0.05)
        try:
            selected = pyperclip.paste()
        except Exception:
            selected = _SENTINEL
        if selected != _SENTINEL:
            break

    # Restore the user's original clipboard shortly after (non-blocking).
    def _restore():
        time.sleep(0.3)
        try:
            pyperclip.copy(previous)
        except Exception:
            pass
    threading.Thread(target=_restore, daemon=True).start()

    if selected == _SENTINEL:
        return ""  # nothing was copied -> nothing was selected
    return selected


def clean_word(text):
    """Reduce a raw selection to a single lookup-able word."""
    text = (text or "").strip()
    if not text:
        return ""
    first = text.split()[0]
    cleaned = "".join(c for c in first if c.isalpha() or c in "-'")
    return cleaned.strip("-'").lower()


# ---------------------------------------------------------------------------
# Dictionary lookup
# ---------------------------------------------------------------------------
def _fetch(url, retries=2):
    """GET the URL, retrying on transient errors (network failures and 5xx).
    Returns the response, or None if the request never got through.
    A 404 is returned immediately — it's a definitive 'no such word'."""
    resp = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, timeout=6)
        except requests.RequestException:
            resp = None
        else:
            # Definitive answers: don't retry.
            if resp.status_code < 500:
                return resp
        if attempt < retries:
            time.sleep(0.6 * (attempt + 1))  # simple backoff
    return resp


def lookup(word, lang):
    """Return a formatted definition string, or a friendly message."""
    url = API.format(lang=lang, word=requests.utils.quote(word))
    resp = _fetch(url)

    if resp is None:
        return (f"{word}\n\nCouldn't reach the dictionary.\n"
                "Check your internet connection.")
    if resp.status_code == 404:
        return f"{word}\n\nNo definition found."
    if resp.status_code != 200:
        return (f"{word}\n\nThe dictionary service is having trouble "
                f"(HTTP {resp.status_code}).\nPlease try again in a moment.")

    try:
        entry = resp.json()[0]
    except (ValueError, IndexError, KeyError, TypeError):
        return f"{word}\n\nNo definition found."

    phonetic = entry.get("phonetic") or ""
    if not phonetic:
        for p in entry.get("phonetics", []):
            if p.get("text"):
                phonetic = p["text"]
                break

    header = entry.get("word", word)
    if phonetic:
        header += f"   {phonetic}"

    lines = [header, ""]
    for meaning in entry.get("meanings", [])[:MAX_PARTS_OF_SPEECH]:
        pos = meaning.get("partOfSpeech", "")
        if pos:
            lines.append(pos)
        for i, d in enumerate(meaning.get("definitions", [])[:MAX_DEFS_PER_PART], 1):
            definition = (d.get("definition") or "").strip()
            if definition:
                lines.append(f"  {i}. {definition}")
        lines.append("")

    result = "\n".join(lines).rstrip()
    return result or f"{word}\n\nNo definition found."


def make_hotkey_handler(lang):
    """Build the callback that runs on the hotkey (listener) thread."""
    def on_hotkey():
        raw = get_selected_text()
        if raw is None:
            _result_q.put(
                "Clipboard unavailable.\n\n"
                "On Linux, install a clipboard tool:\n"
                "  sudo apt install xclip   (or xsel)"
            )
            return
        word = clean_word(raw)
        if not word:
            _result_q.put(
                "No word selected.\n\n"
                "Select a word first, then press the hotkey."
            )
            return
        _result_q.put(lookup(word, lang))
    return on_hotkey


# ---------------------------------------------------------------------------
# Popup UI (all Tk work happens on the main thread)
# ---------------------------------------------------------------------------
class PopupManager:
    def __init__(self, root, timeout_s):
        self.root = root
        self.timeout_ms = int(timeout_s * 1000)
        self.popup = None

    def poll(self):
        try:
            while True:
                text = _result_q.get_nowait()
                self.show(text)
        except queue.Empty:
            pass
        self.root.after(80, self.poll)

    def show(self, text):
        self.close()

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#1e1e1e")

        frame = tk.Frame(win, bg="#1e1e1e", padx=14, pady=12,
                         highlightbackground="#3a3a3a", highlightthickness=1)
        frame.pack()

        tk.Label(
            frame, text=text, justify="left", anchor="w",
            bg="#1e1e1e", fg="#f0f0f0",
            font=("Segoe UI", 11), wraplength=380,
        ).pack(fill="x")

        tk.Label(
            frame, text="Esc / click to close",
            bg="#1e1e1e", fg="#777777", font=("Segoe UI", 8),
        ).pack(anchor="e", pady=(8, 0))

        # Position near the cursor, nudged to stay fully on screen.
        win.update_idletasks()
        px, py = self.root.winfo_pointerxy()
        w, h = win.winfo_width(), win.winfo_height()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = min(px + 12, sw - w - 10)
        y = min(py + 12, sh - h - 10)
        win.geometry(f"+{max(x, 10)}+{max(y, 10)}")

        for widget in (win, frame):
            widget.bind("<Button-1>", lambda e: self.close())
        win.bind("<Escape>", lambda e: self.close())
        try:
            win.focus_force()
        except tk.TclError:
            pass
        win.after(self.timeout_ms, self.close)
        self.popup = win

    def close(self):
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
            self.popup = None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def normalize_hotkey(h):
    """Accept both 'ctrl+shift+d' and '<ctrl>+<shift>+d' styles."""
    aliases = {"control": "ctrl", "command": "cmd", "win": "cmd",
               "super": "cmd", "option": "alt"}
    mods = {"ctrl", "shift", "alt", "cmd"}
    out = []
    for part in h.replace(" ", "").split("+"):
        p = part.strip("<>").lower()
        p = aliases.get(p, p)
        out.append(f"<{p}>" if p in mods else p)
    return "+".join(out)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Show the meaning of the selected word via a global hotkey."
    )
    parser.add_argument("--hotkey", default=DEFAULT_HOTKEY,
                        help=f"Hotkey combo (default: {DEFAULT_HOTKEY}).")
    parser.add_argument("--lang", default=DEFAULT_LANG,
                        help=f"Dictionary language code (default: {DEFAULT_LANG}).")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help="Seconds before the popup auto-closes "
                             f"(default: {DEFAULT_TIMEOUT_S}).")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    hotkey = normalize_hotkey(args.hotkey)

    root = tk.Tk()
    root.withdraw()

    manager = PopupManager(root, args.timeout)
    root.after(80, manager.poll)

    try:
        listener = keyboard.GlobalHotKeys({hotkey: make_hotkey_handler(args.lang)})
        listener.start()
    except Exception as exc:
        sys.exit(f"Couldn't register the hotkey {args.hotkey!r}: {exc}")

    print("Get Meaning is running.")
    print(f"  Select a word anywhere, then press:  {args.hotkey}")
    print("  Press Ctrl+C here (or close this window) to quit.")

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            listener.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
