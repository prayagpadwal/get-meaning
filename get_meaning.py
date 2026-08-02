#!/usr/bin/env python3
"""
Get Meaning — a tiny, cross-platform background utility that shows the
definition of the currently selected word anywhere on your computer
(your browser, the Claude app, Word, a PDF, a code editor, anything).

How it works:
  1. Select a word in any application.
  2. Press the hotkey (default: Alt+Shift+D).
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
import tkinter.font as tkfont  # noqa: E402

# ---------------------------------------------------------------------------
# Defaults (all overridable via command-line flags — see main())
# ---------------------------------------------------------------------------
DEFAULT_HOTKEY = "<alt>+<shift>+d"
DEFAULT_LANG = "en"
DEFAULT_TIMEOUT_S = 12
API = "https://api.dictionaryapi.dev/api/v2/entries/{lang}/{word}"
MAX_PARTS_OF_SPEECH = 3
MAX_DEFS_PER_PART = 2

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
COPY_MODIFIER = Key.cmd if IS_MAC else Key.ctrl

# ---------------------------------------------------------------------------
# Popup look & feel
# ---------------------------------------------------------------------------
UI_FONT = "Segoe UI" if IS_WIN else ("Helvetica Neue" if IS_MAC else "DejaVu Sans")
THEME = {
    "card":      "#23252b",   # card background
    "border":    "#3a3d45",   # card outline
    "word":      "#f6f7f8",   # the looked-up word
    "phonetic":  "#9aa0a6",   # /prəˌnʌnsiˈeɪʃən/
    "accent":    "#8ab4f8",   # part-of-speech tags + list numbers
    "definition":"#dfe1e5",   # definition text
    "hint":      "#6b7078",   # "Esc / click to close"
}
# A colour we don't otherwise use, made fully transparent on Windows so the
# card can have real rounded corners. Other platforms fall back to a plain
# (still rounded-looking) card on a solid background.
_TRANSPARENT = "#010203"
_PAD = 18            # inner padding
_DEF_WRAP = 360      # definition wrap width (px)
_NUM_COL = 24        # width reserved for list numbers
_RADIUS = 14         # corner radius

# A value we never expect to see selected — lets us reliably detect the case
# where the copy did nothing (e.g. no word was selected).
_SENTINEL = "\x00__get_meaning_no_selection__\x00"

# Work items flow from the hotkey thread -> the main (UI) thread via this queue.
# Each item is a tuple: ("loading", token, word) or ("result", token, payload).
_result_q: "queue.Queue" = queue.Queue()
_kbctl = Controller()

# A monotonic token identifies the most recent request, so a slow lookup that
# resolves after a newer keypress can be discarded instead of overwriting it.
_current_token = 0
_token_lock = threading.Lock()


def _next_token():
    global _current_token
    with _token_lock:
        _current_token += 1
        return _current_token


# ---------------------------------------------------------------------------
# Grabbing the selected word
# ---------------------------------------------------------------------------
def _send_copy():
    """Simulate the platform's copy shortcut, releasing any hotkey modifiers
    that might interfere first."""
    time.sleep(0.05)
    # The user may still be holding the hotkey (e.g. Alt+Shift+D). Release the
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


def _msg(title, message):
    """Build a simple message payload (errors, 'no word selected', etc.)."""
    return {"title": title, "message": message}


def lookup(word, lang):
    """Return a structured payload for the popup to render.

    Success: {"title", "phonetic", "meanings": [{"pos", "defs": [...]}, ...]}
    Otherwise: {"title", "message"} (rendered as a plain note)."""
    url = API.format(lang=lang, word=requests.utils.quote(word))
    resp = _fetch(url)

    if resp is None:
        return _msg(word, "Couldn't reach the dictionary.\n"
                          "Check your internet connection.")
    if resp.status_code == 404:
        return _msg(word, "No definition found.")
    if resp.status_code != 200:
        return _msg(word, "The dictionary service is having trouble "
                          f"(HTTP {resp.status_code}).\nPlease try again in a moment.")

    try:
        entry = resp.json()[0]
    except (ValueError, IndexError, KeyError, TypeError):
        return _msg(word, "No definition found.")

    phonetic = entry.get("phonetic") or ""
    if not phonetic:
        for p in entry.get("phonetics", []):
            if p.get("text"):
                phonetic = p["text"]
                break

    meanings = []
    for meaning in entry.get("meanings", [])[:MAX_PARTS_OF_SPEECH]:
        pos = meaning.get("partOfSpeech", "")
        defs = []
        for d in meaning.get("definitions", [])[:MAX_DEFS_PER_PART]:
            definition = (d.get("definition") or "").strip()
            if definition:
                defs.append(definition)
        if defs:
            meanings.append({"pos": pos, "defs": defs})

    if not meanings:
        return _msg(word, "No definition found.")

    return {
        "title": entry.get("word", word),
        "phonetic": phonetic,
        "meanings": meanings,
    }


def make_hotkey_handler(lang):
    """Build the callback that fires when the hotkey is pressed.

    The work runs on a background thread so the key listener stays responsive.
    Crucially, the selection is copied *before* any popup appears — the popup
    grabs keyboard focus, so copying afterwards would target the popup instead
    of the user's text."""
    def on_hotkey():
        token = _next_token()

        def work():
            raw = get_selected_text()
            if raw is None:
                _result_q.put(("result", token, _msg(
                    "Clipboard unavailable",
                    "On Linux, install a clipboard tool:\n"
                    "sudo apt install xclip   (or xsel)")))
                return
            word = clean_word(raw)
            if not word:
                _result_q.put(("result", token, _msg(
                    "No word selected",
                    "Select a word first, then press the hotkey.")))
                return
            # Show the panel immediately (with the word + a spinner) ...
            _result_q.put(("loading", token, word))
            # ... then fill in the definition once the network responds.
            _result_q.put(("result", token, lookup(word, lang)))

        threading.Thread(target=work, daemon=True).start()

    return on_hotkey


# ---------------------------------------------------------------------------
# Popup UI (all Tk work happens on the main thread)
# ---------------------------------------------------------------------------
class PopupManager:
    def __init__(self, root, timeout_s):
        self.root = root
        self.timeout_ms = int(timeout_s * 1000)
        self.popup = None
        self.canvas = None
        self._popup_token = None
        self._anchor = (0, 0)
        self._loading = False
        self._spin_center = (0, 0, 0)
        self._anim_id = None
        self._timeout_id = None
        f = lambda **kw: tkfont.Font(root=root, family=UI_FONT, **kw)
        self.f_word = f(size=17, weight="bold")
        self.f_phon = f(size=11, slant="italic")
        self.f_pos = f(size=10, weight="bold")
        self.f_num = f(size=11, weight="bold")
        self.f_def = f(size=11)
        self.f_hint = f(size=8)

    def poll(self):
        try:
            while True:
                kind, token, data = _result_q.get_nowait()
                if kind == "loading":
                    self.show_loading(token, data)
                elif kind == "result":
                    self.present(token, data)
        except queue.Empty:
            pass
        self.root.after(60, self.poll)

    # -- drawing helpers ----------------------------------------------------
    @staticmethod
    def _round_rect(c, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return c.create_polygon(pts, smooth=True, tags="card", **kw)

    def _footer(self, c, y, w):
        c.create_text(w - _PAD, y + 12, text="Esc / click to close", anchor="ne",
                      font=self.f_hint, fill=THEME["hint"])
        full = c.bbox("all")
        return max(w, full[2] + _PAD), full[3] + _PAD

    def _render(self, c, payload):
        """Draw a result payload; return the (width, height) needed."""
        x0, y = _PAD, _PAD
        title = payload.get("title")
        phonetic = payload.get("phonetic")
        meanings = payload.get("meanings")
        message = payload.get("message")
        sep_y = None

        if title:
            wid = c.create_text(x0, y, text=title, anchor="nw",
                                font=self.f_word, fill=THEME["word"])
            b = c.bbox(wid)
            if phonetic:
                c.create_text(b[2] + 10, b[3] - 2, text=phonetic, anchor="sw",
                              font=self.f_phon, fill=THEME["phonetic"])
            y = b[3] + 9
            sep_y = y
            y += 12

        if meanings:
            for m in meanings:
                pos = (m.get("pos") or "").upper()
                if pos:
                    pid = c.create_text(x0, y, text=pos, anchor="nw",
                                        font=self.f_pos, fill=THEME["accent"])
                    y = c.bbox(pid)[3] + 6
                for i, d in enumerate(m["defs"], 1):
                    c.create_text(x0 + 2, y, text=f"{i}", anchor="nw",
                                  font=self.f_num, fill=THEME["accent"])
                    did = c.create_text(x0 + _NUM_COL, y, text=d, anchor="nw",
                                        font=self.f_def, fill=THEME["definition"],
                                        width=_DEF_WRAP)
                    y = c.bbox(did)[3] + 9
                y += 6
            y -= 6
        elif message:
            mid = c.create_text(x0, y, text=message, anchor="nw",
                                font=self.f_def, fill=THEME["definition"],
                                width=_DEF_WRAP)
            y = c.bbox(mid)[3]

        content = c.bbox("all")
        w = max(content[2] + _PAD, 240)
        if sep_y is not None:
            c.create_line(_PAD, sep_y, w - _PAD, sep_y, fill=THEME["border"])
        return self._footer(c, y, w)

    def _render_loading(self, c, word):
        """Draw the 'looking up...' state; return the (width, height) needed."""
        x0, y = _PAD, _PAD
        sep_y = None
        if word:
            wid = c.create_text(x0, y, text=word, anchor="nw",
                                font=self.f_word, fill=THEME["word"])
            y = c.bbox(wid)[3] + 9
            sep_y = y
            y += 12

        tid = c.create_text(x0 + 26, y, text="Looking up…", anchor="nw",
                            font=self.f_def, fill=THEME["phonetic"])
        tb = c.bbox(tid)
        self._spin_center = (x0 + 9, (tb[1] + tb[3]) // 2, 7)
        y = tb[3]

        content = c.bbox("all")
        w = max(content[2] + _PAD, 220)
        if sep_y is not None:
            c.create_line(_PAD, sep_y, w - _PAD, sep_y, fill=THEME["border"])
        return self._footer(c, y, w)

    def _spin(self, c, angle):
        if not self._loading or self.canvas is not c:
            return
        cx, cy, r = self._spin_center
        c.delete("spinner")
        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=angle, extent=100,
                     style="arc", outline=THEME["accent"], width=3, tags="spinner")
        self._anim_id = self.root.after(55, lambda: self._spin(c, (angle - 30) % 360))

    def _fade_in(self, win, step=0):
        try:
            alpha = min(1.0, (step + 1) * 0.25)
            win.attributes("-alpha", alpha)
        except tk.TclError:
            return
        if alpha < 1.0:
            win.after(12, lambda: self._fade_in(win, step + 1))

    # -- window lifecycle ---------------------------------------------------
    def _create_window(self):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        canvas_bg = THEME["card"]
        if IS_WIN:
            win.configure(bg=_TRANSPARENT)
            try:
                win.attributes("-transparentcolor", _TRANSPARENT)
                canvas_bg = _TRANSPARENT  # lets the rounded corners show through
            except tk.TclError:
                win.configure(bg=THEME["card"])
        else:
            win.configure(bg=THEME["card"])
        try:
            win.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        c = tk.Canvas(win, bg=canvas_bg, highlightthickness=0, bd=0)
        c.pack()
        return win, c

    def _draw_card_bg(self, c, w, h):
        self._round_rect(c, 1, 1, w - 1, h - 1, _RADIUS,
                         fill=THEME["card"], outline=THEME["border"], width=1)
        c.tag_lower("card")
        c.config(width=w, height=h)

    def _clamp(self, x, y, w, h, win):
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = max(10, min(x, sw - w - 12))
        y = max(10, min(y, sh - h - 12))
        return x, y

    def _bind_and_reveal(self, win):
        # Close on a click anywhere inside, on Escape, or when focus is lost
        # (i.e. the user clicked outside the popup).
        for widget in (win, self.canvas):
            widget.bind("<Button-1>", lambda e: self.close())
        win.bind("<Escape>", lambda e: self.close())
        try:
            win.focus_force()
        except tk.TclError:
            pass
        # Arm the click-outside behaviour slightly late so the popup grabbing
        # focus on open doesn't immediately dismiss itself.
        win.after(300, lambda: self._arm_focus_out(win))
        self._fade_in(win)

    def _arm_focus_out(self, win):
        if self.popup is win:
            win.bind("<FocusOut>", lambda e: self.close())

    def _start_timeout(self):
        self._cancel_timeout()
        self._timeout_id = self.root.after(self.timeout_ms, self.close)

    def _cancel_timeout(self):
        if self._timeout_id is not None:
            try:
                self.root.after_cancel(self._timeout_id)
            except tk.TclError:
                pass
            self._timeout_id = None

    def _cancel_anim(self):
        self._loading = False
        if self._anim_id is not None:
            try:
                self.root.after_cancel(self._anim_id)
            except tk.TclError:
                pass
            self._anim_id = None

    # -- public -------------------------------------------------------------
    def show_loading(self, token, word):
        if token != _current_token:
            return  # a newer request already superseded this one
        self.close()
        win, c = self._create_window()
        self.popup, self.canvas, self._popup_token = win, c, token
        self._loading = True

        w, h = self._render_loading(c, word)
        self._draw_card_bg(c, w, h)

        win.update_idletasks()
        px, py = self.root.winfo_pointerxy()
        x, y = self._clamp(px + 14, py + 16, w, h, win)
        self._anchor = (x, y)
        win.geometry(f"{w}x{h}+{x}+{y}")

        self._bind_and_reveal(win)
        self._spin(c, 0)  # no auto-close timeout while still loading

    def present(self, token, payload):
        if token != _current_token:
            return
        if (self.popup is not None and self.canvas is not None
                and self._popup_token == token):
            self._update_in_place(payload)
        else:
            self._show_fresh(token, payload)

    def _update_in_place(self, payload):
        """Replace a loading popup's content with the result, keeping its spot."""
        self._cancel_anim()
        win, c = self.popup, self.canvas
        c.delete("all")
        w, h = self._render(c, payload)
        self._draw_card_bg(c, w, h)
        win.update_idletasks()
        x, y = self._clamp(self._anchor[0], self._anchor[1], w, h, win)
        win.geometry(f"{w}x{h}+{x}+{y}")
        self._start_timeout()

    def _show_fresh(self, token, payload):
        """Show a result with no preceding loading popup (e.g. 'no word')."""
        self.close()
        win, c = self._create_window()
        self.popup, self.canvas, self._popup_token = win, c, token
        self._loading = False
        w, h = self._render(c, payload)
        self._draw_card_bg(c, w, h)
        win.update_idletasks()
        px, py = self.root.winfo_pointerxy()
        x, y = self._clamp(px + 14, py + 16, w, h, win)
        self._anchor = (x, y)
        win.geometry(f"{w}x{h}+{x}+{y}")
        self._bind_and_reveal(win)
        self._start_timeout()

    def close(self):
        self._cancel_anim()
        self._cancel_timeout()
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
        self.popup = None
        self.canvas = None
        self._popup_token = None


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
