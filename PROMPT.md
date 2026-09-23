# Project Prompt

Paste everything below the line into a new Claude Code session opened in this repo (start in plan mode).

---

## ⚠️ ACCOUNT SAFETY — HIGHEST PRIORITY, OVERRIDES EVERYTHING ELSE
I do NOT want my real account banned. Every feature must be designed so the game client and Cygames' servers cannot tell this app exists. If a feature can't be built safely, leave it out and tell me why. Never take a shortcut here.

HARD RULES (never break these, even if I ask later in this project):
1. No touching the game process: no memory reading or writing (e.g. pymem, Cheat Engine-style scanning), no DLL injection, no hooking (DirectX/overlay hooks, Frida), no attaching debuggers, and no reading the game's process or window handles.
2. No network interception: no packet sniffing, proxies, MITM, or decrypting game traffic (e.g. scapy, mitmproxy, pydivert, Fiddler).
3. No automation of the game: no simulated clicks, keypresses, or macros (e.g. pyautogui, SendInput, AutoHotkey), no auto-training, and no auto-racing. The app only *recommends*. I make every move myself.
4. No modifying game files: no editing, patching, or even reading the game's install folder, cache, or save data.
5. No using my account outside the game: never ask for, store, or use my login, session tokens, or cookies. Never call Cygames/game servers directly.
6. No in-game overlay: any "overlay" is a normal, separate Windows window of this app, not drawn inside the game.
7. Data comes in only through: (a) screenshots I take myself with the Steam/Windows screenshot key, read from a normal image folder; (b) manual entry; (c) public community websites/datasets accessed anonymously.
8. Anything that could be read as "sharing" my data (e.g. uploading a profile) must be opt-in and anonymous, and must never include my account ID or friend code unless I type it in myself.

ENFORCEMENT:
- Keep a `SAFETY.md` listing these rules, and state at the top of each module how it complies.
- Add a safety self-test that scans the codebase and fails if it finds banned libraries or APIs (pymem, frida, scapy, mitmproxy, pydivert, pyautogui, pynput keyboard/mouse control, ctypes calls to ReadProcessMemory/WriteProcessMemory/SendInput/OpenProcess, or any path pointing at the game's install folder). Run it before every build.
- If I ever request a feature that would break these rules, stop and warn me instead of building it, and suggest a safe alternative.
- Use the project agents in `.claude/agents/` as described in `CLAUDE.md` (safety-auditor after every feature, ocr-tester after importer changes, game-data-researcher for data updates).

---

Build a local, offline-first advisor app called **Trainer Notebook** for Umamusume: Pretty Derby (Global/Steam version) that runs on my Windows 11 PC. It is ADVISORY ONLY and follows the safety rules above.

## Getting my account data (safe methods only)
1. Screenshot/OCR import: read only from a folder of screenshots I took myself (Steam F12 / Win+PrtScn) of my Support Card list, Uma list, Veteran/Legacy (parent) list, and inventory (carrots, tickets, spark counts). Recognize cards and umas by matching card art or name text, and read limit-break level, star level, and so on. Never capture the screen or game window itself.
2. Manual editor: a simple UI to fix any wrong OCR results or add items by hand, with search and autocomplete from the game database.
3. Save everything to a local JSON/SQLite "account profile" so I only update what changed.
4. Veteran detail import: screenshot each veteran's spark/factor screen so the app can read spark types and star counts.

## Game database
- Pull support cards, umas, skills, events, races, and banners from a public community data source (e.g. GameTora or a similar open dataset). Cache it locally and include an "update data" button.
- Track which banners are live on Global versus already released in JP, so it can forecast upcoming Global banners from JP history. Show the forecast as an estimated date range and label it as a guess.

## Features
1. **Meta tier list**: rate support cards and umas for the current Global meta, including the Champions Meeting / Team Trials format, at each limit-break level. Explain why each one ranks where it does.
2. **Deck builder**: given my owned cards and a target uma, distance, and style, recommend the best 6-card deck (including a friend/borrow slot) and the best pair of parents from my veterans. Score each deck by its expected stats and skill hints.
3. **Training run advisor** (the "best possible run"): a manual-input turn helper. I enter the current turn's stats, the training options with their support cards and failure %, mood, and energy, and it recommends the best action, using a scoring model or Monte-Carlo simulation of the rest of the run. It also covers event choice recommendations and a race schedule planner.
4. **Skill point optimizer**: given my skill points and available skills, pick the skill set that gives the best value for a target race (a knapsack-style solver).
5. **Pull advisor**:
   - Exact probability calculator for a given number of pulls (SSR rate, rate-up rate, pity/spark at 200), e.g. "P(at least 1 copy)" and "P(MLB)", with a chart.
   - "Pull or save?" recommendation based on my current collection, how good the card or uma is in the meta, my carrot count, and upcoming forecast banners.
   - Carrot and spark tracker, plus an income projection (daily and event carrots) so I can see how many carrots I'll have by a future banner.
6. **Veteran (trained uma) cleanup advisor**: rank my trained/veteran umas from "keep" to "safe to delete" so I can free up storage.
   - Score each veteran on:
     - Blue spark (stat) type and stars. 3★ Speed/Stamina/Power are valuable.
     - Pink spark (aptitude) type and stars. Distance and track sparks that fix common weak aptitudes rank highest.
     - Green (unique skill) spark and stars, and whether that uma's unique skill is meta.
     - Useful white sparks (skills and races), weighted by how meta each skill is.
     - Whether it is in a current Team Trials or Champions Meeting lineup, or on my rental/profile slot.
     - Overall rank/rating, and whether it is my best copy of that character.
     - Parent compatibility (affinity) with the umas I'm likely to train next.
   - Output 4 tiers, each with a one-line reason:
     - **Keep (locked)**: in active use, or top parent material.
     - **Low priority**: decent, but I own better options.
     - **Consider removing**: weak sparks, or duplicated by a better veteran.
     - **Trash**: no useful sparks, low rank, not used anywhere.
   - Never recommend deleting a veteran that is locked, in a team lineup, or the only source of a spark I need for a planned parent pair.
   - Let me pin/protect umas manually and set a storage target (e.g. "free up 20 slots"). It then suggests the lowest-value set to remove to reach that target.
   - The app only shows recommendations; it never deletes anything itself. I do the deleting in the game.
7. **Extra tools & QoL** (build after the core features, one at a time):
   - Daily/weekly checklist with local-timezone reset countdowns.
   - Event planner + shop priority list per currency.
   - Support card limit-break planner (best use of spare copies/shop currency).
   - Target stat calculator per race/distance/style.
   - Event choice lookup (search or OCR the event name).
   - Run log: save final stats/deck/parents per run; use the history to tune advisor weights.
   - Parent pair planner for building a target spark set.
   - Champions Meeting prep per upcoming course, and Team Trials lineup optimizer.
   - Approximate race simulator vs a typical meta field (clearly labeled estimate).
   - "What if I pull" preview: how my decks/tiers change if I got a given card/uma.
   - Pull history log with actual vs expected luck, spark/pity progress.
   - Collection gap report by card type, ranked by impact.
   - QoL: global hotkey that opens this app's own window (never sends keys to the game), a separate always-on-top lookup window (no game injection), auto-import from my screenshot folder, JSON backup/export with change history, alerts when recommendations change after a data update, dark mode, global search, EN/JP names.
8. **Dashboard**: one screen showing my carrots, the next recommended banner, account gaps (e.g. "you lack a good Wit card"), and the top deck for each distance.

## Tech
- Python 3.12. Local web UI (FastAPI + a simple HTML/JS frontend, or Streamlit if that's simpler). SQLite for storage. Tesseract or a template-matching OCR that runs offline.
- Keep it simple and in few files. Include a README with setup steps and a small self-test for the probability math and deck scoring.
- Where exact game formulas aren't publicly known, use documented community approximations, mark them clearly in the code, and make the weights editable.
- This repo is public: my account profile, screenshots, and database must stay gitignored.

Start by creating `SAFETY.md` and the safety self-test, then propose the file layout and data model, then build it one feature at a time, starting with the account import and the pull probability calculator.
