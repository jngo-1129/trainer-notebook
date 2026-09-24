"""Always-on-top lookup window + global hotkey (Ctrl+Alt+U) for Trainer Notebook.

Run: .venv\\Scripts\\pythonw lookup.py   (needs app.py running)

Safety (rules 1, 3, 6): this is a normal, separate window of this app. The hotkey is registered with
RegisterHotKey, which only tells Windows to notify *this* program when the combo is pressed. It never
hooks the keyboard, never reads or sends keys to other windows, and never touches the game's window or
process. Lookups go to this app's own local server (127.0.0.1) only.
"""
import ctypes
import json
import queue
import threading
import tkinter as tk
import urllib.parse
import urllib.request
import webbrowser
from ctypes import wintypes

APP = "http://127.0.0.1:8765"
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
VK_U = 0x55
WM_HOTKEY = 0x0312


_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route lookups through a proxy


def search(q: str) -> list[dict]:
    with _LOCAL.open(f"{APP}/api/search?q={urllib.parse.quote(q)}", timeout=5) as r:
        return json.load(r)


def events(q: str) -> list[dict]:
    with _LOCAL.open(f"{APP}/api/events?q={urllib.parse.quote(q)}", timeout=5) as r:
        return json.load(r)


def hotkey_thread(q: queue.Queue):
    """Waits for our own hotkey message; posts to the Tk thread through a queue."""
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_U):
        q.put("hotkey-failed")
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == WM_HOTKEY:
            q.put("toggle")


def main():
    root = tk.Tk()
    root.title("Trainer Notebook lookup")
    root.attributes("-topmost", True)
    root.geometry("420x360")
    entry = tk.Entry(root, font=("Segoe UI", 11))
    entry.pack(fill="x", padx=8, pady=8)
    out = tk.Text(root, wrap="word", font=("Segoe UI", 10), height=16)
    out.pack(fill="both", expand=True, padx=8)
    bar = tk.Frame(root)
    bar.pack(fill="x", padx=8, pady=6)
    tk.Button(bar, text="Open Trainer Notebook", command=lambda: webbrowser.open(APP)).pack(side="left")
    status = tk.Label(bar, text="Ctrl+Alt+U shows/hides this window", fg="gray")
    status.pack(side="right")

    def show(text: str):
        out.delete("1.0", "end")
        out.insert("end", text)

    def run(_=None):
        q = entry.get().strip()
        if len(q) < 2:
            return
        try:
            ev = events(q)[:2]
            lines = []
            for e in ev:  # event choices first: that's what you need mid-run
                lines.append(f"EVENT  {e['name']}  ({e['source']})")
                for o in e["options"]:
                    lines.append(f"  {'->' if o['best'] else '  '} {o['option']}: {o['effects'].replace(chr(10), ', ')}")
            lines += [f"{r['kind'].upper():7} {r['label']}  {r['extra']}" for r in search(q) if r["kind"] != "event"][:12]
            show("\n".join(lines) or "No match.")
        except OSError:
            show("Can't reach Trainer Notebook. Start it with: .venv\\Scripts\\python app.py")

    entry.bind("<Return>", run)
    q: queue.Queue = queue.Queue()
    threading.Thread(target=hotkey_thread, args=(q,), daemon=True).start()

    def poll():
        while not q.empty():
            m = q.get()
            if m == "hotkey-failed":
                status.config(text="Ctrl+Alt+U is taken by another program")
            elif root.state() == "withdrawn":
                root.deiconify()
                root.lift()
                entry.focus_force()
                entry.select_range(0, "end")
            else:
                root.withdraw()
        root.after(100, poll)

    poll()
    entry.focus_set()
    root.mainloop()


if __name__ == "__main__":
    main()
