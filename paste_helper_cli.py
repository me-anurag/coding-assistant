"""Minimal terminal version. F9 = type clipboard, F10 = stop, Ctrl+C = quit."""
import threading
import time

import pyperclip
from pynput import keyboard

DELAY = 0.02
START_WAIT = 0.4

kb = keyboard.Controller()
stop_flag = threading.Event()
lock = threading.Lock()


def type_clipboard():
    if not lock.acquire(blocking=False):
        return
    try:
        stop_flag.clear()
        text = pyperclip.paste().replace("\r\n", "\n")
        if not text:
            print("Clipboard is empty.")
            return
        time.sleep(START_WAIT)
        for ch in text:
            if stop_flag.is_set():
                print("Stopped.")
                return
            kb.type(ch)
            time.sleep(DELAY)
        print(f"Typed {len(text)} characters.")
    finally:
        lock.release()


print("Click into the text box, then press F9. F10 = stop. Ctrl+C here = quit.")
with keyboard.GlobalHotKeys({
    "<f9>": lambda: threading.Thread(target=type_clipboard, daemon=True).start(),
    "<f10>": stop_flag.set,
}) as h:
    h.join()
