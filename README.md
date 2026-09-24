# Trainer Notebook

A local, offline-first helper for Umamusume: Pretty Derby (Global/Steam). It only gives advice and never touches the game client. See [SAFETY.md](SAFETY.md) for the rules every feature follows.

## Setup (Windows 11)

```bash
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python test.py
.venv\Scripts\python app.py
```

Open http://127.0.0.1:8765. Go to **Settings → Update game data** once. After that the app works offline.

Optional always-on-top lookup window with a Ctrl+Alt+U hotkey (needs `app.py` running):

```bash
.venv\Scripts\pythonw lookup.py
```

**Screenshots:** take them yourself with Steam F12 or Win+PrtScn. Put them in `./screenshots`, or set another folder on the Import tab.

## What's in it

| Tab | What it does |
|---|---|
| Dashboard | Carats and projection, next recommended banner, account gaps, top deck per distance, current CM cup, reset countdowns, alerts when recommendations change after a data update |
| Tiers | Support cards (model score at each LB), umas by distance, and community skill tiers. Each shows the model tier next to the community tier with a reason |
| Deck | Best 6-card deck (borrow slot optional) from your collection for a trainee and race or CM cup. Shows stat focus vs target, useful hint skills, and the best parent pair from your veterans |
| Training | Turn helper (type the numbers shown on each training), event choice lookup, race schedule with career objectives |
| Skills | Skill point knapsack for a target race. Gold/◎ skills include their base skill's cost |
| Pulls | Exact pull odds with spark, live and forecast banners (JP→Global fit, labeled as a guess), pull-or-save advice, carat income projection |
| Collection / Veterans | Manual editor. Veterans also has the cleanup advisor (keep → trash, "free up N slots"), parent pair planner, and Team Trials lineup |
| Tools | Daily/weekly checklist, event & shop planner, LB planner, collection gap report, what-if pull preview, target stats and race simulator, CM prep, run log with weight tuning, pull history with luck |
| Import | OCR suggestions from your screenshots, event lookup from a screenshot, auto-check for new screenshots |
| Settings | Game data update, model weights (JSON overrides), EN/JP names, reset times, JSON backup/restore, change history |

Global search sits in the header, and the theme button cycles auto → light → dark.

## Files

| File | What it does |
|---|---|
| `safety_check.py` | Scans the repo and installed packages for banned APIs, game paths, and non-allow-listed URLs. Runs on app start and in `test.py`. |
| `app.py` | FastAPI server on 127.0.0.1 and the JSON API |
| `static/index.html` | The whole UI (vanilla JS, no CDN) |
| `db.py` | Profile in SQLite (`profile/notebook.db`), change history, backup/restore |
| `gamedata.py` | Downloads and caches public data from GameTora and UmaTools in `data/cache/` |
| `data/meta.json` | Community tiers, banners, CM cups, and income, researched from public sites with sources (committed) |
| `importer.py` | Offline OCR of your screenshots (RapidOCR) |
| `pulls.py` | Exact pull odds |
| `model.py` | Support card, uma and skill value, deck builder, parents. All constants are in `WEIGHTS` |
| `advisor.py` | Training turn scoring, events, race plan, skill knapsack |
| `banners.py` | Banner forecast, income, pull-or-save |
| `veterans.py` | Cleanup advisor, parent planner, Team Trials lineup |
| `racesim.py` | Approximate race simulator and stamina calculator |
| `tools.py` | Dashboard, checklist resets, LB planner, gaps, what-if, luck, run tuning, CM prep, search, alerts |
| `lookup.py` | Always-on-top lookup window + Ctrl+Alt+U hotkey |
| `evaluate.py` | Backtests the banner forecast and validates the card model against expert tiers; writes `docs/EVALUATION.md` |
| `data/tier_labels.json` | Game8's full tier ladder used as evaluation labels. **Not in the repo** (third-party editorial content); regenerate with the `game-data-researcher` agent |
| `test.py` | Runs all self-tests |

## Data model (`profile/notebook.db`, gitignored)

- `owned(kind, id, level)`: support cards (limit break 0–4) and umas (stars 1–5), keyed by GameTora ID.
- `veteran(id, card_id, rating, sparks, locked, pinned, lineup, rental, note)`: `sparks` is JSON `[{"color", "name", "stars"}]`.
- `kv(key, value)`: inventory, settings, and small JSON lists (checklist, planner, planned parents, next trainees, weight overrides).
- `run`, `pull_log`: run log and pull history.
- `history`: every change, for the backup/undo trail.
- `imported(file, mtime)`: screenshots already reviewed.

## How good is the advice?

Measured, not assumed: see **[docs/EVALUATION.md](docs/EVALUATION.md)** (regenerate with `python evaluate.py`).

- **Banner forecast:** rolling-origin backtest, held-out mean error of about 3 days (vs about 16 for a naive baseline), with a calibrated interval.
- **Support card model:** agrees with Game8's within-type rankings on 62% of card pairs with hand-set weights and 72% after calibration on held-out cards. Recency baselines score 47–51%.

- **Pull math:** exact.
- **Card, deck, skill, training and race scores:** community-style approximations. The game's formulas aren't public. Every constant is in `model.WEIGHTS` / `advisor.ADV` / `veterans.VET` and can be overridden in Settings. The community tier column (Game8, dated, in `data/meta.json`) is the reality check.
- **Friend/Group cards:** get no model tier, because their mechanics are scenario-specific.
- **Banner forecast:** a backtested straight-line fit, always shown as a date range and labeled an estimate. Officially announced dates override it.
- **Race simulator:** idealized (no positioning, blocking, slopes or randomness).
- **OCR:** reads text only (titles, spark names, numbers, event titles). Limit-break pips and star icons need real sample screenshots in `tests/screenshots/` to add image matching.
- **Parent affinity:** ignores grandparents, which aren't stored.
