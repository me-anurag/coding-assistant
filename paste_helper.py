"""
Paste Helper (Python only, no browser extension)

A tiny always-on-top strip:   [ q ] [ co ] [ > ]
  - Select text on a site, press F8 (copy), then click  q  or  co  to store it.
    A dot appears after the label when it holds content:  q•   co•
  - Clicking a label again overwrites its content.
  - "x" opens/closes a white box showing the text F9 would type (scrollable, to read and copy by hand).
  - ">" starts a new session (clears both).
The strip never takes focus, so your selection / text box stays active.

Hotkeys (global):
  F8  = copy the selection
  F9  = type whatever is on the clipboard into the focused box
  Ctrl+Alt+Q = type stored "q" content
  Ctrl+Alt+C = type stored "co" content
  F10 = stop typing

The normal window is only the settings panel; it can sit in the background.
"""
import ctypes
import random
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from pynput import keyboard
from pynput.keyboard import Key

import llm_helper
from formatter import describe, extract_fragment, has_formatting, html_to_ops

try:
    import win32clipboard as wc
    import win32gui
except ImportError:          # not Windows / pywin32 missing -> plain text only
    wc = None
    win32gui = None

IDE_NAMES = ("visual studio code", "vscodium", "cursor", "pycharm", "intellij", "webstorm", "clion",
             "android studio", "sublime", "visual studio", "atom", "eclipse", "netbeans", "spyder", "jupyter",
             # online editors / coding sites (matched in the browser tab title)
             "leetcode", "geeksforgeeks", "hackerrank", "hackerearth", "codeforces", "codechef", "replit",
             "onlinegdb", "programiz", "jdoodle", "codepen", "jsfiddle", "codesandbox", "colab", "kaggle",
             "coderpad", "codility", "interviewbit", "codestudio", "coding ninjas", "stackblitz", "playground",
             "compiler", "online ide", "editor")


_CODE_LINE = re.compile(r"^\s*(def|class|for|while|if|elif|else|try|except|import|from|return|#include|"
                        r"public|private|function|const|let|var|int|void)\b", re.M)


def looks_like_code(text):
    """Multi-line text that looks like source code (indented lines, braces, keywords)."""
    if text.count("\n") < 1:
        return False
    if re.search(r"^(?:[ ]{2,}|\t)\S", text, re.M):
        return True
    if re.search(r"[{};]\s*$", text, re.M):
        return True
    return len(_CODE_LINE.findall(text)) >= 2


def foreground_is_ide():
    """True if the focused window looks like a code editor (matched by title)."""
    if win32gui is None:
        return False
    try:
        title = win32gui.GetWindowText(win32gui.GetForegroundWindow()).lower()
    except Exception:
        return False
    return any(n in title for n in IDE_NAMES)

IS_WIN = sys.platform == "win32"
PLAIN = (False, False, False)
FMT_KEYS = ("b", "i", "u")
SLOTS = ("q", "co")


# ---------- Windows clipboard helpers ----------
def _open_clipboard(retries=15):
    for _ in range(retries):
        try:
            wc.OpenClipboard()
            return True
        except Exception:
            time.sleep(0.03)
    return False


def clipboard_html():
    """HTML fragment on the clipboard, or None."""
    if wc is None:
        return None
    try:
        fmt = wc.RegisterClipboardFormat("HTML Format")
        if not _open_clipboard():
            return None
        try:
            if not wc.IsClipboardFormatAvailable(fmt):
                return None
            data = wc.GetClipboardData(fmt)
        finally:
            wc.CloseClipboard()
        if isinstance(data, str):
            data = data.encode("utf-8", "ignore")
        return extract_fragment(data)
    except Exception:
        return None


def clipboard_seq():
    if wc is None:
        return None
    try:
        return wc.GetClipboardSequenceNumber()
    except Exception:
        return None


# ---------- the floating strip ----------
class Strip:
    BG = "#1e1e1e"
    FG = "#ffffff"          # buttons stay white; the dot after the name shows "stored"
    BAD = "#ff5555"

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=self.BG)
        self.win.attributes("-alpha", 0.85)

        self.bar = tk.Frame(self.win, bg=self.BG)
        self.bar.pack(side="top", anchor="w")

        self.labels = {}
        grip = tk.Label(self.bar, text="\u22ee", bg=self.BG, fg="#777", font=("Segoe UI", 9), cursor="fleur")
        grip.pack(side="left", padx=(3, 0))
        grip.bind("<ButtonPress-1>", self._drag_start)
        grip.bind("<B1-Motion>", self._drag_move)

        for name in SLOTS:
            lb = tk.Label(self.bar, text=name, bg=self.BG, fg=self.FG, font=("Segoe UI", 9, "bold"),
                          padx=5, pady=1, cursor="hand2")
            lb.pack(side="left")
            lb.bind("<Button-1>", lambda e, n=name: app.store_slot(n))
            self.labels[name] = lb

        ab = tk.Label(self.bar, text="A", bg=self.BG, fg=self.FG, font=("Segoe UI", 9, "bold"),
                      padx=5, pady=1, cursor="hand2")
        ab.pack(side="left")
        ab.bind("<Button-1>", lambda e: app.ask_answer())
        self.labels["A"] = ab

        xb = tk.Label(self.bar, text="x", bg=self.BG, fg=self.FG, font=("Segoe UI", 9, "bold"),
                      padx=5, pady=1, cursor="hand2")
        xb.pack(side="left")
        xb.bind("<Button-1>", lambda e: self.toggle_box("x"))

        self.type_lb = tk.Label(self.bar, text="vs", bg=self.BG, fg="#8ab4ff", font=("Segoe UI", 9, "bold"),
                                padx=5, pady=1, cursor="hand2")
        self.type_lb.pack(side="left")
        self.type_lb.bind("<Button-1>", lambda e: app.toggle_editor_type())

        nxt = tk.Label(self.bar, text=">", bg=self.BG, fg="#8ab4ff", font=("Segoe UI", 9, "bold"),
                       padx=5, pady=1, cursor="hand2")
        nxt.pack(side="left", padx=(0, 3))
        nxt.bind("<Button-1>", lambda e: app.new_session())

        # ----- the white text box (what F9 would type), hidden until "x" is clicked -----
        self.font = tkfont.Font(family="Consolas", size=10)
        self.box = tk.Frame(self.win, bg="#ffffff")
        self.text = tk.Text(self.box, height=3, width=20, wrap="none", bg="#ffffff", fg="#000000",
                            font=self.font, relief="flat", bd=0, padx=4, pady=2, insertwidth=0,
                            cursor="arrow", state="disabled")
        self.vsb = tk.Scrollbar(self.box, orient="vertical", command=self.text.yview, width=12)
        self.hsb = tk.Scrollbar(self.box, orient="horizontal", command=self.text.xview, width=12)
        self.text.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb.grid(row=1, column=0, sticky="ew")
        self.text.bind("<MouseWheel>", self._wheel)
        self.text.bind("<Shift-MouseWheel>", self._hwheel)
        self.box_visible = False
        self.box_src = None                      # "x" = clipboard text, "A" = AI answer
        self._shown = None
        self._poll_id = None

        self.reset_position()
        self.win.update_idletasks()
        self._make_noactivate()
        self._keep_on_top()

    # ----- text box -----
    def _wheel(self, e):
        self.text.yview_scroll(-1 if e.delta > 0 else 1, "units")
        return "break"

    def _hwheel(self, e):
        self.text.xview_scroll(-3 if e.delta > 0 else 3, "units")
        return "break"

    def render(self, raw, empty="(empty)"):
        shown = raw.expandtabs(4) if raw else empty
        lines = shown.split("\n")
        longest = max(len(l) for l in lines)
        cw = max(1, self.font.measure("0"))
        cap = max(20, (self.win.winfo_screenwidth() - 80) // cw)
        self.text.config(width=max(12, min(longest, cap)))
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", shown)
        self.text.config(state="disabled")
        self.text.xview_moveto(0)
        self.text.yview_moveto(0)
        # scrollbars only when they are needed
        (self.vsb.grid if len(lines) > 3 else self.vsb.grid_remove)()
        (self.hsb.grid if longest > cap else self.hsb.grid_remove)()

    def _clip(self):
        return self.app.get_clipboard().replace("\r\n", "\n").replace("\r", "\n")

    def fill(self):
        raw = self._clip()
        self._shown = raw
        self.render(raw, "(clipboard is empty)")

    def _poll(self):
        if not self.box_visible or self.box_src != "x":
            return
        try:
            if self._clip() != self._shown:
                self.fill()
        except Exception:
            pass
        self._poll_id = self.win.after(500, self._poll)

    def _stop_poll(self):
        if self._poll_id:
            self.win.after_cancel(self._poll_id)
            self._poll_id = None

    def collapse(self):
        self.box_visible = False
        self.box_src = None
        self.box.pack_forget()
        self._stop_poll()

    def show(self, src, raw=None):
        """Open the box (or switch it) to 'x' (clipboard) or 'A' (AI answer)."""
        self._stop_poll()
        self.box_src = src
        if src == "x":
            self.fill()
        else:
            self.render(raw or "", "(no answer yet)")
        if not self.box_visible:
            self.box.pack(side="top", anchor="w")
            self.box_visible = True
        if src == "x":
            self._poll_id = self.win.after(500, self._poll)

    def toggle_box(self, src="x"):
        if self.box_visible and self.box_src == src:
            self.collapse()
        else:
            self.show(src)

    # ----- position / drag -----
    def reset_position(self):
        self.win.update_idletasks()
        w = self.win.winfo_reqwidth()
        x = (self.win.winfo_screenwidth() - w) // 2
        self.win.geometry(f"+{x}+2")

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y()

    def _drag_move(self, e):
        self.win.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # never steal focus from the page (Windows)
    def _hwnd(self):
        h = ctypes.windll.user32.GetParent(self.win.winfo_id())
        return h or self.win.winfo_id()

    def _make_noactivate(self):
        if not IS_WIN:
            return
        try:
            u = ctypes.windll.user32
            hwnd = self._hwnd()
            ex = u.GetWindowLongW(hwnd, -20)
            u.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x00000080)   # NOACTIVATE | TOOLWINDOW
        except Exception:
            pass

    def _keep_on_top(self):
        """Re-assert topmost every second so it stays above full-screen pages."""
        try:
            if self.win.winfo_viewable():
                if IS_WIN:
                    # HWND_TOPMOST, NOMOVE|NOSIZE|NOACTIVATE
                    ctypes.windll.user32.SetWindowPos(self._hwnd(), -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
                else:
                    self.win.lift()
        except Exception:
            pass
        self.win.after(1000, self._keep_on_top)

    # ----- indicators -----
    def set_stored(self, name, stored):
        self.labels[name].config(text=name + "\u2022" if stored else name, fg=self.FG)

    def set_type(self, etype):
        self.type_lb.config(text=etype, fg="#8ab4ff" if etype == "vs" else "#ffb454")

    def flash_error(self, name):
        lb = self.labels[name]
        lb.config(fg=self.BAD)
        self.win.after(500, lambda: lb.config(fg=self.FG))

    def set_visible(self, show):
        if show:
            self.win.deiconify()
            self._make_noactivate()
        else:
            self.win.withdraw()

    def set_alpha(self, a):
        self.win.attributes("-alpha", a)


# ---------- main app (settings window) ----------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.kb = keyboard.Controller()
        self.stop_flag = threading.Event()
        self.busy = threading.Lock()
        self.slots = {n: None for n in SLOTS}     # name -> typing plan
        self.answer = None                        # last AI answer (cleaned text)
        self.answer_key = None                    # (q_text, co_text) it was made from
        self._asking = False
        self.editor_type = "vs"                   # "vs" = VS Code style, "ot" = other (keeps previous indent)

        root.title("Paste Helper - Settings")
        root.geometry("480x760")        # normal window: NOT topmost, can stay in background

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Clipboard preview:").pack(anchor="w")
        self.preview = tk.Text(frm, height=7, wrap="word", state="disabled")
        self.preview.pack(fill="both", expand=True, pady=(2, 4))

        row = ttk.Frame(frm)
        row.pack(fill="x")
        self.info = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.info, foreground="#0a5").pack(side="left")
        ttk.Button(row, text="Refresh", command=self.refresh_preview).pack(side="right")

        ttk.Label(frm, text="Stored slots:").pack(anchor="w", pady=(8, 0))
        self.slot_info = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self.slot_info, foreground="#444", wraplength=440).pack(anchor="w")

        opts = ttk.Frame(frm)
        opts.pack(fill="x", pady=8)
        ttk.Label(opts, text="Delay per char (ms):").grid(row=0, column=0, sticky="w")
        self.delay_var = tk.IntVar(value=20)
        ttk.Spinbox(opts, from_=0, to=500, textvariable=self.delay_var, width=6).grid(row=0, column=1, padx=6)
        ttk.Label(opts, text="Countdown (s):").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.count_var = tk.IntVar(value=3)
        ttk.Spinbox(opts, from_=0, to=30, textvariable=self.count_var, width=4).grid(row=0, column=3, padx=6)

        self.fmt_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, variable=self.fmt_var,
                        text="Keep formatting (Ctrl+B/I/U) - turn OFF for plain text boxes",
                        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.random_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Randomize delay (human-like)", variable=self.random_var).grid(
            row=2, column=0, columnspan=4, sticky="w")
        ttk.Label(opts, text="Code editor mode:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.ide_var = tk.StringVar(value="Auto")
        ttk.Combobox(opts, textvariable=self.ide_var, values=("Auto", "On", "Off"), width=7,
                     state="readonly").grid(row=3, column=1, padx=6, pady=(6, 0))
        ttk.Label(opts, text="(Auto = detects editors / code text; use On if unsure)", foreground="#666").grid(
            row=3, column=2, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(opts, text="Editor line delay (ms):").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.line_delay_var = tk.IntVar(value=80)
        ttk.Spinbox(opts, from_=20, to=500, textvariable=self.line_delay_var, width=6).grid(
            row=4, column=1, padx=6, pady=(6, 0))
        ttk.Label(opts, text="(raise it if indent still creeps forward)", foreground="#666").grid(
            row=4, column=2, columnspan=2, sticky="w", pady=(6, 0))

        strip_box = ttk.LabelFrame(frm, text="Strip", padding=8)
        strip_box.pack(fill="x", pady=4)
        self.show_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(strip_box, text="Show strip", variable=self.show_var,
                        command=lambda: self.strip.set_visible(self.show_var.get())).grid(row=0, column=0, sticky="w")
        ttk.Button(strip_box, text="Reset position", command=lambda: self.strip.reset_position()).grid(
            row=0, column=1, padx=10)
        ttk.Label(strip_box, text="Opacity:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.alpha_var = tk.DoubleVar(value=0.85)
        ttk.Scale(strip_box, from_=0.3, to=1.0, variable=self.alpha_var,
                  command=lambda v: self.strip.set_alpha(float(v))).grid(row=1, column=1, sticky="we", pady=(6, 0))

        cfg = llm_helper.load_config()
        ai_box = ttk.LabelFrame(frm, text="AI answer (A button) - OpenRouter, settings in the .env file", padding=8)
        ai_box.pack(fill="x", pady=4)
        self.key_info = tk.StringVar(value="")
        ttk.Label(ai_box, textvariable=self.key_info, foreground="#444").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(ai_box, text="Model:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.model_var = tk.StringVar(value=cfg["model"])
        ttk.Entry(ai_box, textvariable=self.model_var, width=38).grid(row=1, column=1, sticky="we", padx=6, pady=(4, 0))
        ttk.Label(ai_box, text="Default language:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.lang_var = tk.StringVar(value=cfg["language"])
        ttk.Entry(ai_box, textvariable=self.lang_var, width=24).grid(row=2, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Button(ai_box, text="Reload .env", command=self.reload_env).grid(row=2, column=2, sticky="e", pady=(4, 0))
        ai_box.columnconfigure(1, weight=1)
        self._update_key_info(cfg)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Type clipboard (countdown)", command=lambda: self.start(countdown=True)).pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(btns, text="Stop", command=self.stop).pack(side="left", padx=(4, 0))

        self.status = tk.StringVar(value="F8 copy | F9 type clipboard | Ctrl+Alt+Q / Ctrl+Alt+C type q / co | F10 stop")
        ttk.Label(frm, textvariable=self.status, wraplength=440, foreground="#444").pack(anchor="w", pady=(8, 0))

        self.strip = Strip(self)
        self.refresh_preview()
        self.refresh_slots()

        self.hotkeys = keyboard.GlobalHotKeys({
            "<f8>": lambda: root.after(0, self.copy_selection),
            "<f9>": lambda: root.after(0, lambda: self.start(countdown=False)),
            "<f10>": lambda: root.after(0, self.stop),
            "<ctrl>+<alt>+q": lambda: root.after(0, lambda: self.type_slot("q")),
            "<ctrl>+<alt>+c": lambda: root.after(0, lambda: self.type_slot("co")),
        })
        self.hotkeys.start()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(300, root.iconify)           # settings window starts in the background

    # ---------- clipboard / preview ----------
    def get_clipboard(self) -> str:
        try:
            return self.root.clipboard_get()
        except tk.TclError:
            return ""

    def refresh_preview(self):
        text = self.get_clipboard()
        shown = text if len(text) <= 2000 else text[:2000] + "\n... (truncated preview)"
        self.preview.config(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", shown if text else "(clipboard is empty)")
        self.preview.config(state="disabled")

        if wc is None:
            self.info.set("Formatting detection needs pywin32 (pip install pywin32)")
            return
        html = clipboard_html()
        if not html:
            self.info.set("Plain text (no formatting on clipboard)")
            return
        found = describe(html_to_ops(html))
        self.info.set(f"Formatting found: {found}" if found else "Plain text (no bold/italic/underline)")

    def refresh_slots(self):
        parts = []
        for n in SLOTS:
            plan = self.slots[n]
            if plan:
                chars = len(plan["text"])
                parts.append(f"{n}: {chars} chars")
            else:
                parts.append(f"{n}: empty")
        self.slot_info.set("   |   ".join(parts))

    def set_status(self, msg: str):
        self.root.after(0, lambda: self.status.set(msg))

    # ---------- slots (q / co / new session) ----------
    def build_plan(self):
        text = self.get_clipboard().replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return None
        plan = [(text, PLAIN)]
        if self.fmt_var.get():
            html = clipboard_html()
            if html:
                ops = html_to_ops(html)
                if ops and has_formatting(ops):
                    plan = ops
        # keep the raw text too: code editor mode types it as plain text
        return {"ops": plan, "text": text}

    def store_slot(self, name):
        plan = self.build_plan()
        if not plan:
            self.strip.flash_error(name)
            self.status.set(f"Nothing on the clipboard to store as {name}. Select text, press F8, then click {name}.")
            return
        self.slots[name] = plan
        self.strip.set_stored(name, True)
        if self.answer_key != self._qco():
            self.strip.set_stored("A", False)       # inputs changed: the old answer no longer matches
        self.refresh_slots()
        self.status.set(f"Stored as {name}.")

    # ---------- AI answer (A button) ----------
    def _update_key_info(self, cfg):
        key = cfg["api_key"]
        if not key or "PASTE-YOUR-KEY" in key:
            self.key_info.set("API key: NOT SET - put OPENROUTER_API_KEY in .env, then Reload .env")
        else:
            self.key_info.set(f"API key: found ({key[:9]}...)  | fallbacks: {len(cfg['fallbacks'])}")

    def ai_config(self):
        cfg = llm_helper.load_config()
        cfg["model"] = self.model_var.get().strip() or cfg["model"]      # fields override .env for this session
        cfg["language"] = self.lang_var.get().strip() or cfg["language"]
        return cfg

    def reload_env(self):
        cfg = llm_helper.load_config(force=True)
        self.model_var.set(cfg["model"])
        self.lang_var.set(cfg["language"])
        self._update_key_info(cfg)
        self.status.set(".env reloaded.")

    def _qco(self):
        q, co = self.slots["q"], self.slots["co"]
        return (q["text"] if q else ""), (co["text"] if co else "")

    def ask_answer(self):
        if self.strip.box_visible and self.strip.box_src == "A":
            self.strip.collapse()
            return
        qt, ct = self._qco()
        if not (qt.strip() or ct.strip()):
            self.strip.show("A", "Store text in q and/or co first.")
            return
        key = (qt, ct)
        if self.answer is not None and self.answer_key == key:
            self.strip.show("A", self.answer)
            return
        if self._asking:
            self.strip.show("A", "Still thinking...")
            return
        self.strip.show("A", "Thinking...")
        self._asking = True
        cfg = self.ai_config()
        threading.Thread(target=self._ask_worker, args=(key, cfg), daemon=True).start()

    def _ask_worker(self, key, cfg):
        try:
            ans, err = llm_helper.solve(key[0], key[1], cfg), None
        except llm_helper.LLMError as e:
            ans, err = None, str(e)
        except Exception as e:                      # never let the thread die silently
            ans, err = None, f"Unexpected error: {e}"
        self.root.after(0, lambda: self._ask_done(key, ans, err))

    def _ask_done(self, key, ans, err):
        self._asking = False
        showing = self.strip.box_visible and self.strip.box_src == "A"
        if err:
            if showing:
                self.strip.show("A", "Error: " + err)
            self.status.set("AI: " + err)
            return
        self.answer, self.answer_key = ans, key
        if key == self._qco():
            self.strip.set_stored("A", True)
        if showing:
            self.strip.show("A", ans)
        self.status.set("AI answer ready. Click A to view it.")

    def toggle_editor_type(self):
        self.editor_type = "ot" if self.editor_type == "vs" else "vs"
        self.strip.set_type(self.editor_type)
        self.status.set("Editor type: " + ("vs (VS Code style)" if self.editor_type == "vs"
                                           else "ot (other: empty lines are laid out first, then filled in)"))

    def new_session(self):
        self.stop_flag.set()
        self.slots = {n: None for n in SLOTS}
        self.answer = self.answer_key = None
        for n in SLOTS + ("A",):
            self.strip.set_stored(n, False)
        if self.strip.box_src == "A":
            self.strip.collapse()
        self.refresh_slots()
        self.status.set("New session: everything reset.")

    def type_slot(self, name):
        plan = self.slots.get(name)
        if not plan:
            self.status.set(f"{name} is empty.")
            return
        self.start(countdown=False, plan=plan)

    # ---------- copy (F8) ----------
    def copy_selection(self):
        threading.Thread(target=self._copy_worker, daemon=True).start()

    def _combo(self, key):
        with self.kb.pressed(Key.ctrl):
            self.kb.press(key)
            self.kb.release(key)

    def _wait_clipboard_change(self, before, timeout=0.7):
        end = time.time() + timeout
        while time.time() < end:
            if clipboard_seq() != before:
                return True
            time.sleep(0.05)
        return False

    def _copy_worker(self):
        time.sleep(0.2)
        before = clipboard_seq()
        self._combo("c")
        if before is None:
            time.sleep(0.4)
            self.set_status("Copy sent (cannot verify without pywin32).")
            self.root.after(300, self.refresh_preview)
            return
        ok = self._wait_clipboard_change(before)
        if not ok:
            self._combo(Key.insert)       # Ctrl+Insert = copy; some sites only block Ctrl+C
            ok = self._wait_clipboard_change(before)
        if ok:
            self.set_status("Copied. Click q or co on the strip to store it.")
            self.root.after(150, self.refresh_preview)
        else:
            self.set_status("Nothing was copied: the site blocks copying itself. "
                            "Try right-click > Copy, or copy from another source.")

    # ---------- type (F9) ----------
    def start(self, countdown: bool, plan=None):
        if plan is None:
            plan = self.build_plan()
            if not plan:
                self.status.set("Clipboard is empty.")
                return
        if not self.busy.acquire(blocking=False):
            self.status.set("Already typing. Press Stop first.")
            return
        try:
            wait = max(0, self.count_var.get()) if countdown else 0.4
            mode = self.ide_var.get()
            etype = self.editor_type
            self._line_delay = max(20, self.line_delay_var.get()) / 1000.0
            delay = max(0, self.delay_var.get()) / 1000.0
            randomize = self.random_var.get()
        except tk.TclError:
            self.busy.release()
            self.status.set("Invalid delay/countdown value.")
            return
        self.stop_flag.clear()
        threading.Thread(target=self._worker, args=(plan, wait, delay, randomize, mode, etype), daemon=True).start()

    def stop(self):
        self.stop_flag.set()

    def _type_code(self, text, etype, delay, randomize):
        """'vs': Enter, wipe the auto-indent, type the line as is.
        'ot': lay out all empty lines first, then fill them (editor auto-indent never applies)."""
        if etype == "ot":
            return self._type_code_ot(text, delay, randomize)
        d = getattr(self, "_line_delay", 0.08)
        lines = text.split("\n")
        total = len(text)
        done = 0

        def type_str(s):
            nonlocal done
            for ch in s:
                if self.stop_flag.is_set():
                    self.set_status(f"Stopped after {done}/{total} characters.")
                    return False
                try:
                    self.kb.type(ch)
                except Exception:
                    pass
                done += 1
                dd = random.uniform(delay * 0.5, delay * 1.8) if randomize else delay
                if dd:
                    time.sleep(dd)
                if done % 25 == 0:
                    self.set_status(f"Typing... {done}/{total}")
            return True

        for idx, line in enumerate(lines):
            if self.stop_flag.is_set():
                self.set_status(f"Stopped after {done}/{total} characters.")
                return False
            if idx > 0:
                self._ide_newline()
                done += 1
            if not type_str(line):
                return False


        return True

    def _type_code_ot(self, text, delay, randomize):
        """For editors with their own smart indent (keeps/extra-indents after ':' or '{').
        1) press Enter N-1 times on the empty line: Enter on an empty line never auto-indents
        2) go back up to the first of those lines
        3) type each line with its own indentation, then End + Right to reach the next empty line
        Typing never triggers auto-indent, so the result matches the original exactly."""
        d = getattr(self, "_line_delay", 0.08)
        kd = max(0.015, d * 0.3)
        lines = text.split("\n")
        n = len(lines)
        total = len(text)
        done = 0

        def tap(key, times=1):
            for _ in range(times):
                if self.stop_flag.is_set():
                    return False
                self.kb.tap(key)
                time.sleep(kd)
            return True

        self.set_status(f"Preparing {n} lines...")
        if n > 1:
            if not tap(Key.enter, n - 1):
                self.set_status("Stopped.")
                return False
            time.sleep(d)
            if not tap(Key.up, n - 1):
                self.set_status("Stopped.")
                return False
            time.sleep(d)
        for idx, line in enumerate(lines):
            for ch in line:
                if self.stop_flag.is_set():
                    self.set_status(f"Stopped after {done}/{total} characters.")
                    return False
                try:
                    self.kb.type(ch)
                except Exception:
                    pass
                done += 1
                dd = random.uniform(delay * 0.5, delay * 1.8) if randomize else delay
                if dd:
                    time.sleep(dd)
                if done % 25 == 0:
                    self.set_status(f"Typing... {done}/{total}")
            done += 1
            if idx < n - 1:
                self.kb.tap(Key.esc)              # close any suggestion list before moving
                time.sleep(d * 0.5)
                self.kb.tap(Key.end)
                time.sleep(kd)
                self.kb.tap(Key.right)            # past the line break -> start of the next empty line
                time.sleep(kd)
        return True

    def _ide_newline(self):
        """Enter, then wipe the indentation the editor auto-added (the text carries its own)."""
        d = getattr(self, "_line_delay", 0.08)
        self.kb.tap(Key.esc)                 # close an open suggestion list so Enter can't accept it
        time.sleep(d * 0.5)
        self.kb.tap(Key.enter)
        time.sleep(d)                        # let the editor finish its own auto-indent first
        with self.kb.pressed(Key.shift):     # on a whitespace-only line Home goes to column 0
            self.kb.tap(Key.home)
        time.sleep(d * 0.5)                  # selection is replaced by the next character typed

    def _worker(self, plan, wait, delay, randomize, mode="Off", etype="vs"):
        state = [False, False, False]

        def set_fmt(fmt):
            for idx in range(3):
                if fmt[idx] != state[idx]:
                    self._combo(FMT_KEYS[idx])
                    state[idx] = fmt[idx]
                    time.sleep(0.04)

        try:
            remaining = float(wait)
            while remaining > 0:
                if self.stop_flag.is_set():
                    self.set_status("Stopped.")
                    return
                self.set_status(f"Click into the text box... typing in {remaining:.0f}s" if wait >= 1 else "Typing...")
                step = min(1.0, remaining)
                time.sleep(step)
                remaining -= step

            ide = mode == "On" or (mode == "Auto" and (foreground_is_ide() or looks_like_code(plan["text"])))
            if ide:
                # editors have their own auto-indent / shortcuts: type plain text line by line
                # (tabs -> 4 spaces so a Tab key can never re-indent anything)
                text = plan["text"].replace("\t", "    ")
                if self._type_code(text, etype, delay, randomize):
                    self.set_status(f"Done. Typed {len(text)} characters ({etype} mode).")
                return
            ops = plan["ops"]
            total = sum(len(t) for t, _ in ops)
            done = 0
            for text, fmt in ops:
                set_fmt(fmt)
                for ch in text:
                    if self.stop_flag.is_set():
                        self.set_status(f"Stopped after {done}/{total} characters.")
                        return
                    try:
                        if ide and ch == "\n":
                            self._ide_newline()
                        else:
                            self.kb.type(ch)
                    except Exception:
                        pass
                    done += 1
                    d = random.uniform(delay * 0.5, delay * 1.8) if randomize else delay
                    if d:
                        time.sleep(d)
                    if done % 25 == 0:
                        self.set_status(f"Typing... {done}/{total}")
            self.set_status(f"Done. Typed {total} characters.")
        finally:
            try:
                set_fmt(PLAIN)
            except Exception:
                pass
            self.busy.release()

    def quit(self):
        self.stop_flag.set()
        self.hotkeys.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()