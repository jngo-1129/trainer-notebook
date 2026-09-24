# SAFETY.md — account safety rules

**These rules override every feature request.** If a feature can't be built within them, it isn't built.
Trainer Notebook is advisory only: the game client and Cygames' servers must never be able to tell it exists.

## Hard rules

1. **No touching the game process.** No memory reading/writing (pymem, Cheat Engine-style scans), no DLL injection, no hooking (DirectX/overlay hooks, Frida), no debuggers, no reading the game's process or window handles.
2. **No network interception.** No packet sniffing, proxies, MITM, or decrypting game traffic (scapy, mitmproxy, pydivert, Fiddler).
3. **No automation of the game.** No simulated clicks, keypresses, or macros (pyautogui, SendInput, AutoHotkey). No auto-training, no auto-racing. The app only recommends; the player makes every move.
4. **No game files.** Never edit, patch, or read the game's install folder, cache, or save data.
5. **No account use outside the game.** Never ask for, store, or use login details, session tokens, or cookies. Never call Cygames/game servers.
6. **No in-game overlay.** Any "overlay" is a normal, separate window of this app, never drawn inside the game.
7. **Data comes in only through:** (a) screenshots the player takes themselves (Steam F12 / Win+PrtScn), read from a normal image folder; (b) manual entry; (c) public community websites/datasets accessed anonymously.
8. **Sharing is opt-in and anonymous.** Nothing that could be read as sharing (e.g. uploading a profile) happens unless the player turns it on, and it never includes account ID or friend code unless the player types it in.

## Enforcement

- `safety_check.py` scans the codebase and dependencies for banned libraries/APIs, screen capture, game install paths, and any URL outside the allow-list below. It runs:
  - on every app start (`app.py` refuses to start if it fails),
  - as the first step of `python test.py`.
- Every module states at the top how it complies.
- The `safety-auditor` agent reviews the whole repo after every feature.
- A requested feature that breaks these rules gets a warning and a safe alternative instead of an implementation.

## Network allow-list

Only anonymous HTTPS GETs to public community data. No cookies, no auth headers, no game servers.

| Host | Used for |
|---|---|
| `gametora.com` | Game database JSON (cards, umas, skills, races, courses, Champions Meeting schedule, parent affinity, JP/Global release dates) |
| `raw.githubusercontent.com` | Only `daftuyda/UmaTools` public assets: English event choices and career objectives (GameTora's event text is obfuscated, and we don't decode it) |

The app's own web UI binds to `127.0.0.1` only, so it can't be reached from other machines. It also rejects requests whose Host or Origin isn't this app, so other websites open in your browser can't call it. Downloads use a dedicated opener with no proxy and no cookies, check the allow-list before every request, and refuse redirects off it.

## How each feature stays safe

| Feature | Input | Why it's safe |
|---|---|---|
| Screenshot import (`importer.py`) | Image files in a folder you choose | Only reads images you saved. Refuses game/program folders (install paths, folders containing `.exe`/`.dll`). OCR runs offline. |
| Tiers, deck builder, parents (`model.py`) | Your typed collection + public data | Pure math. |
| Training turn helper, events, races, skills (`advisor.py`) | Numbers you read off your screen and type in | Recommends only. You press every button in the game. |
| Pull advisor, forecast (`banners.py`, `pulls.py`) | Your carats + public release dates | Pure math, forecasts labeled as estimates. |
| Veteran cleanup (`veterans.py`) | Your typed veterans | Recommends only. Never deletes anything in the game. Protected veterans are never suggested. |
| Race simulator (`racesim.py`) | Stats you type | Pure math, labeled as an estimate. |
| Lookup window + hotkey (`lookup.py`) | Ctrl+Alt+U, text you type | A separate normal window of this app. `RegisterHotKey` only notifies this program: no keyboard hook, never reads or sends keys to the game, never looks at the game window. |
| Backup / restore (`db.py`) | Your own JSON file | Local file only; nothing is uploaded. |

## Private data (repo is public)

`profile/` (SQLite account profile), `screenshots/`, `data/cache/`, and `*.db` are gitignored and never committed.
