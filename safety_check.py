"""Safety self-test: fail if the codebase could touch the game (see SAFETY.md).

Safety: read-only scan of this repo's own source files and installed package names.
Never looks outside the repo, never touches the game.
Run: python safety_check.py   (exit 1 on any finding)
"""
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).parent
ALLOWED_HOSTS = {"gametora.com", "raw.githubusercontent.com", "127.0.0.1", "localhost", "www.w3.org"}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__"}
SKIP_FILES = {"safety_check.py"}  # holds the banned list itself
CODE_EXT = {".py", ".html", ".js", ".ps1", ".bat", ".cmd", ".ahk"}

# Rules 1-3, 6: process access, network interception, input automation, screen capture/hooking.
BANNED_MODULES = [
    "pymem", "frida", "scapy", "mitmproxy", "pydivert", "pyautogui", "pynput", "keyboard", "mouse",
    "pydirectinput", "pywinauto", "ahk", "interception", "win32api", "win32gui", "win32process",
    "win32con", "win32ui", "pywin32", "psutil", "mss", "pygetwindow", "dxcam", "d3dshot", "pyscreeze",
    "pyscreenshot", "uiautomation", "interception-python", "winreg", "http.cookiejar",
]
BANNED_APIS = [
    "ReadProcessMemory", "WriteProcessMemory", "OpenProcess", "VirtualAllocEx", "CreateRemoteThread",
    "SendInput", "keybd_event", "mouse_event", "SetWindowsHookEx", "FindWindow", "EnumWindows",
    "GetForegroundWindow", "DebugActiveProcess", "ImageGrab", "PrintWindow", "BitBlt", "GetWindowDC",
    "PostMessage", "SendMessage", "SetCursorPos", "EnumProcesses", "CreateToolhelp32Snapshot",
    "Process32First", "NtReadVirtualMemory", "LoadLibrary",
    # reading keys / other windows (lookup.py must stay a self-contained window)
    "GetAsyncKeyState", "GetKeyState", "GetKeyboardState", "RegisterRawInputDevices", "SetWinEventHook",
    "AttachThreadInput", "SetForegroundWindow", "EnumChildWindows", "GetWindowText", "WindowFromPoint",
    "GetWindowThreadProcessId",
]
# Rule 4: game install folder / save data.
BANNED_PATHS = r"steamapps|LocalLow[\\/]+Cygames|umamusume[^\s\"']*\.exe"

_MODS = "|".join(re.escape(m) for m in BANNED_MODULES)
# `from x import`, `import os, x`, `__import__("x")`, `import_module("x")`
IMPORT_RE = re.compile(r"^\s*(?:from\s+(%s)\b|import\s[^\n#]*?\b(%s)\b)|(?:__import__|import_module)\(\s*['\"](%s)\b"
                       % (_MODS, _MODS, _MODS), re.M)
API_RE = re.compile(r"\b(%s)[AW]?\b" % "|".join(BANNED_APIS))  # Win32 ...A/...W variants too
PATH_RE = re.compile(BANNED_PATHS, re.I)
URL_RE = re.compile(r"(?:https?|wss?)://([^/\s\"'<>):]+)([^\s\"'<>)]*)")
PATH_PINNED = {"raw.githubusercontent.com": "/daftuyda/UmaTools/",  # host -> only this path prefix
               "www.w3.org": "/2000/svg"}  # SVG namespace id in generated charts, never fetched


def scan_text(text: str) -> list[str]:
    """Return findings for one file's text."""
    out = [f"banned import '{''.join(m)}'" for m in IMPORT_RE.findall(text)]
    out += [f"banned API '{m}'" for m in API_RE.findall(text)]
    out += [f"game path '{m.group(0)}'" for m in PATH_RE.finditer(text)]
    for host, path in URL_RE.findall(text):
        host = host.lower()
        match = next((h for h in ALLOWED_HOSTS if host == h or host.endswith("." + h)), None)
        pin = PATH_PINNED.get(match)
        if match is None:
            out.append(f"URL host not in allow-list '{host}'")
        elif pin and (host != match or not (path.startswith(pin) if pin.endswith("/") else path == pin)):
            out.append(f"URL path not allowed on {host}: '{path}'")  # pinned hosts: exact host, prefix or exact path
    return out


def scan_repo() -> list[str]:
    findings = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.name in SKIP_FILES or SKIP_DIRS & set(p.relative_to(ROOT).parts):
            continue
        if p.suffix in CODE_EXT:
            findings += [f"{p.relative_to(ROOT)}: {f}" for f in scan_text(p.read_text("utf-8", "replace"))]
        elif p.name == "requirements.txt":
            for line in p.read_text("utf-8").splitlines():
                name = re.split(r"[<>=\[; ]", line.strip(), maxsplit=1)[0].lower()
                if name in BANNED_MODULES:
                    findings.append(f"requirements.txt: banned package '{name}'")
    installed = {(d.metadata["Name"] or "").lower() for d in metadata.distributions()}
    findings += [f"installed package: '{n}'" for n in sorted(installed & set(BANNED_MODULES))]
    return findings


def _selftest():
    """The scanner must catch what it claims to."""
    bad = ("import pyautogui\nctypes.windll.kernel32.ReadProcessMemory\n"
           "fetch('https://api.example.com')\nopen(r'C:\\Steam\\steamapps\\common')")
    assert len(scan_text(bad)) == 4, scan_text(bad)
    for sneaky in ("import os, pyautogui", "__import__('pymem')", "importlib.import_module(\"frida\")",
                   "from http.cookiejar import CookieJar", "user32.PostMessageW", "ws://evil.example/x"):
        assert scan_text(sneaky), sneaky
    assert scan_text("https://raw.githubusercontent.com/someone/else/x.json")
    assert scan_text("from gamedata import x  # https://gametora.com/data") == []
    assert scan_text('<svg xmlns="http://www.w3.org/2000/svg">') == []
    for w3 in ("http://www.w3.org/2000/svg-x", "https://www.w3.org/", "http://a.www.w3.org/2000/svg"):
        assert scan_text(w3), w3


if __name__ == "__main__":
    found = scan_repo()
    for f in found:
        print("FAIL", f)
    print("safety check:", "FAIL" if found else "PASS")
    sys.exit(1 if found else 0)
