"""Pull advisor: Global banner forecast from JP history, carat income projection, pull-or-save advice.

Safety: uses only the cached public DB, data/meta.json (researched public info) and numbers the user typed.

The forecast is a GUESS: a straight-line fit of Global release date vs JP release date over recent cards.
Always shown as a date range and labeled as an estimate.
"""
from datetime import date, timedelta

import gamedata as gd
import model
import pulls
from evaluate import LAUNCH, ols, release_groups

# Chosen by the rolling-origin backtest in evaluate.py (see docs/EVALUATION.md): OLS on the last 30 banners,
# +/-5 days = 80th percentile of backtest errors. Re-run `python evaluate.py` after big schedule changes.
FIT_WINDOW = 30
INTERVAL_DAYS = 5


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def window(b: dict) -> str:
    return f"{b['from']} – {b['to']}" if b["from"] != b["to"] else f"starts {b['from']}"


def forecast(today: date | None = None, count: int = 8) -> dict:
    """Group unreleased-on-Global cards by JP release date and project their Global date."""
    today = today or date.today()
    cards = [("support", c) for c in gd.support_cards().values()] + [("uma", c) for c in gd.uma_cards().values()]
    known = [g for g in release_groups() if LAUNCH < g["gl"] <= today]
    if len(known) < 5:
        return {"banners": [], "note": "not enough data"}
    recent = sorted(known, key=lambda g: g["gl"])[-FIT_WINDOW:]
    icpt, slope = ols([(g["jp"].toordinal(), g["gl"].toordinal()) for g in recent])
    spread = INTERVAL_DAYS
    pairs = [(g["jp"], g["gl"]) for g in recent]
    last_jp = max(j for j, _ in pairs)
    pending: dict[date, list] = {}
    for kind, c in cards:
        if c["rarity"] != 3 or not c.get("release_jp") or (c.get("release_gl") and _d(c["release_gl"]) <= today):
            continue
        jp = _d(c["release_jp"])
        if jp >= last_jp - timedelta(days=60):
            pending.setdefault(jp, []).append({"kind": kind, "id": c["id"], "label": f"{c['title']} {c['name']}",
                                               "announced": c.get("release_gl")})
    announced_ids = {(bn["kind"], i): bn["start"] for bn in gd.curated().get("banners", [])
                     if bn.get("end", "") >= today.isoformat() for i in bn.get("featured", [])}
    for group in pending.values():  # officially announced dates (data/meta.json) beat the guess
        for x in group:
            x["announced"] = x["announced"] or announced_ids.get((x["kind"], x["id"]))
    out = []
    for jp in sorted(pending)[:count]:
        mid = date.fromordinal(round(icpt + slope * jp.toordinal()))
        mid = max(mid, today)
        announced = next((x["announced"] for x in pending[jp] if x["announced"]), None)
        out.append({"jp_date": jp.isoformat(), "cards": pending[jp], "estimate": not announced,
                    "from": announced or (mid - timedelta(days=spread)).isoformat(),
                    "to": announced or (mid + timedelta(days=spread)).isoformat()})
    return {"banners": out, "pace": round(slope, 2),
            "note": f"Estimate: Global has been releasing JP content at {slope:.2f}x JP's pace (last {len(pairs)} banners). "
                    f"+/-{spread} days covered 88% of releases in a held-out backtest (docs/EVALUATION.md)."}


def income(kv: dict, until: date, today: date | None = None) -> dict:
    """Projected carats on a date. Rates default to data/meta.json income, editable in kv."""
    today = today or date.today()
    inc = gd.curated().get("income", {})
    daily = float(kv.get("daily_carats") or inc.get("daily_carats") or 0)
    weekly_extra = float(kv.get("weekly_extra_carats") or max(0, (inc.get("weekly_carats") or 0) - daily * 7))
    pass_daily = float(inc.get("monthly_pass_carats_per_day") or 0) if kv.get("monthly_pass") in ("1", "true") else 0
    days = max(0, (until - today).days)
    now = int(float(kv.get("carats") or 0))
    total = now + round(days * (daily + pass_daily) + days // 7 * weekly_extra)
    tickets = int(float(kv.get("tickets_support") or 0))
    return {"date": until.isoformat(), "days": days, "carats_now": now, "carats": total, "pulls": total // pulls.CARATS_PER_PULL,
            "rates": {"daily": daily, "weekly_extra": weekly_extra, "pass_daily": pass_daily}, "tickets_support": tickets}


def _support_gain(card_id: int, owned: dict[int, int], w: dict, dists=model.DISTANCES) -> list[float]:
    """Best-deck improvement at each final LB 0-4 (max over distances)."""
    base = {d: model.build_deck(owned, {"distance_cat": d}, None, w, borrow=False)["score"] for d in dists}
    gains = []
    for lb in range(5):
        trial = owned | {card_id: max(lb, owned.get(card_id, -1))}
        gains.append(max(model.build_deck(trial, {"distance_cat": d}, None, w, borrow=False)["score"] - base[d] for d in dists))
    return gains


def advise(card_id: int, owned: dict[int, int], kv: dict, w: dict, rate: float = pulls.RATE_UP,
           horizon_days: int = 60) -> dict:
    """Pull or save for one support card. Compares expected deck gain now vs the best forecast banner."""
    card = gd.support_cards().get(card_id)
    if not card:
        return {"error": "unknown card"}
    budget = income(kv, date.today())
    n = min(budget["pulls"] + budget["tickets_support"], 200)
    have = owned.get(card_id, -1)  # -1 = not owned; each copy adds one LB
    gains = _support_gain(card_id, owned, w)

    def expected_gain(pulls_n: int, gain_by_lb: list[float], have_lb: int) -> float:
        probs = [pulls.p_copies(k, pulls_n, rate) - pulls.p_copies(k + 1, pulls_n, rate) for k in range(5)]
        probs.append(pulls.p_copies(5, pulls_n, rate))
        return sum(p * (gain_by_lb[min(4, have_lb + k)] if have_lb + k >= 0 else 0) for k, p in enumerate(probs))

    now = expected_gain(n, gains, have)
    future = []
    for b in forecast()["banners"]:
        if b["from"] > (date.today() + timedelta(days=horizon_days)).isoformat():
            break
        for c in b["cards"]:
            if c["kind"] == "support" and c["id"] != card_id:
                g = _support_gain(c["id"], owned, w)
                future.append({"label": c["label"], "window": window(b), "estimate": b["estimate"],
                               "gain": round(expected_gain(min(income(kv, _d(b['from']))['pulls'], 200), g, owned.get(c['id'], -1)), 1)})
    future.sort(key=lambda f: -f["gain"])
    best_future = future[0] if future else None
    tier = next((t for t in gd.curated().get("support_tiers", []) if t["support_id"] == card_id), None)
    threshold = w.get("pull_threshold", 15)
    if now >= threshold and (not best_future or now >= 0.8 * best_future["gain"]):
        verdict = "Pull"
        why = f"expected +{now:.0f} deck score with {n} pulls"
    elif best_future and best_future["gain"] > now:
        verdict = "Save"
        why = f"{best_future['label']} ({best_future['window']}{', estimate' if best_future['estimate'] else ''}) looks better: +{best_future['gain']:.0f} vs +{now:.0f}"
    else:
        verdict = "Skip"
        why = f"small upgrade for your decks (+{now:.0f})"
    return {"card": gd.label("support", card_id), "verdict": verdict, "why": why, "pulls_available": n,
            "gain_by_lb": [round(g, 1) for g in gains], "expected_gain": round(now, 1),
            "p_first_copy": pulls.p_copies(1, n, rate), "p_mlb": pulls.p_copies(max(0, 4 - have), n, rate) if have >= 0 else pulls.p_copies(5, n, rate),
            "community": tier, "future": future[:5]}


def advise_uma(card_id: int, owned_umas: set[int], kv: dict, w: dict, rate: float = pulls.RATE_UP) -> dict:
    """Pull or skip for a trainee: does it beat your best owned uma at any distance?"""
    umas = gd.uma_cards()
    u = umas.get(card_id)
    if not u:
        return {"error": "unknown uma"}
    if card_id in owned_umas:
        return {"card": gd.label("uma", card_id), "verdict": "Skip", "why": "already owned (extra copies only give shards)"}
    gains = {}
    for d in model.DISTANCES:
        best = max((model.uma_value(umas[i], d, w)["score"] for i in owned_umas if i in umas), default=0)
        gains[d] = model.uma_value(u, d, w)["score"] - best
    budget = income(kv, date.today())
    n = min(budget["pulls"] + int(float(kv.get("tickets_uma") or 0)), 200)
    tier = next((t for t in gd.curated().get("uma_tiers", []) if t["card_id"] == card_id), None)
    top = max(gains, key=gains.get)
    good = gains[top] > 0 or (tier and tier["tier"] in "SA")
    return {"card": gd.label("uma", card_id), "verdict": "Pull" if good else "Skip",
            "why": (f"beats your best {top} uma by {gains[top]:.0f}" if gains[top] > 0 else
                    f"community tier {tier['tier']}: {tier['reason']}" if good else "you already own better options"),
            "gains": {d: round(g) for d, g in gains.items()}, "pulls_available": n,
            "p_first_copy": pulls.p_copies(1, n, rate), "community": tier}


def _selftest():
    if not gd.support_cards():
        print("banners selftest: SKIP (no game data)")
        return
    f = forecast()
    assert f["banners"], f
    assert all(b["from"] <= b["to"] for b in f["banners"])
    inc = income({"carats": "1500", "daily_carats": "50", "weekly_extra_carats": "0"}, date(2026, 1, 11), date(2026, 1, 1))
    assert inc["carats"] == 2000 and inc["pulls"] == 13, inc
    print("banners selftest: PASS")


if __name__ == "__main__":
    _selftest()
