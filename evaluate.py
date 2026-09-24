"""Model evaluation: (1) rolling-origin backtest of the Global banner forecast,
(2) support-card scoring model vs community tier labels, with held-out weight calibration.

Safety: offline analysis of the cached public data and data/*.json only. No game access, no network.
Run: python evaluate.py   -> prints results and writes docs/EVALUATION.md
"""
import json
import random
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median, pstdev

import gamedata as gd

ROOT = Path(__file__).parent
LAUNCH = date(2025, 6, 26)  # Global launch: ~35 SSRs released at once, not a normal release decision
HORIZON = 90                # evaluate predictions for banners up to 90 days ahead


# ---------------------------------------------------------------- forecast backtest

def release_groups() -> list[dict]:
    """SSR releases grouped by JP date (one JP date ~ one banner). gl = first Global release of the group."""
    groups: dict[date, list] = {}
    for cards in (gd.support_cards(), gd.uma_cards()):
        for c in cards.values():
            if c["rarity"] == 3 and c.get("release_jp") and c.get("release_gl"):
                groups.setdefault(date.fromisoformat(c["release_jp"][:10]), []).append(date.fromisoformat(c["release_gl"][:10]))
    return sorted(({"jp": jp, "gl": min(gls), "n": len(gls)} for jp, gls in groups.items()), key=lambda g: g["jp"])


def ols(pairs):
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 1.0
    return my - b * mx, b


def _theil_sen(pairs):
    slopes = [(y2 - y1) / (x2 - x1) for i, (x1, y1) in enumerate(pairs) for x2, y2 in pairs[i + 1:] if x2 != x1]
    b = median(slopes) if slopes else 1.0
    return median(y - b * x for x, y in pairs), b


def _line(fit, n=None):
    """Model: fit a line gl ~ jp on the n most recent releases (all if None)."""
    def model(train, jp):
        pairs = [(g["jp"].toordinal(), g["gl"].toordinal()) for g in sorted(train, key=lambda g: g["gl"])[-(n or len(train)):]]
        a, b = fit(pairs)
        return a + b * jp.toordinal()
    return model


def fixed_lag(train, jp):
    return jp.toordinal() + median((g["gl"] - g["jp"]).days for g in train)


def last_lag(train, jp):
    last = max(train, key=lambda g: (g["gl"], g["jp"]))
    return jp.toordinal() + (last["gl"] - last["jp"]).days


MODELS = {
    "fixed lag (median, baseline)": fixed_lag,
    "last lag (naive, baseline)": last_lag,
    "OLS, all history": _line(ols),
    "OLS, last 30": _line(ols, 30),
    "OLS, last 15": _line(ols, 15),
    "OLS, last 8": _line(ols, 8),
    "Theil-Sen, last 30": _line(_theil_sen, 30),
    "Theil-Sen, last 15": _line(_theil_sen, 15),
}
APP_V1 = "OLS, last 30"  # closest to the app's original method (OLS on the last 60 SSR cards ~ 30 banners)


def backtest(groups: list[dict], today: date, step: int = 7, min_train: int = 8) -> list[dict]:
    """One row per (cutoff, model, target banner): what the model predicted using only data known at the cutoff."""
    post = [g for g in groups if g["gl"] > LAUNCH]
    cutoffs, t = [], LAUNCH + timedelta(days=step)
    while t < today:
        if sum(g["gl"] <= t for g in post) >= min_train:
            cutoffs.append(t)
        t += timedelta(days=step)
    rows = []
    for t in cutoffs:
        train = [g for g in post if g["gl"] <= t]
        targets = [g for g in groups if t < g["gl"] <= min(today, t + timedelta(days=HORIZON))]
        for name, model in MODELS.items():
            for g in targets:
                pred = max(model(train, g["jp"]), t.toordinal() + 1)  # can't predict the past
                rows.append({"cutoff": t, "model": name, "jp": g["jp"], "actual": g["gl"],
                             "ahead": (g["gl"] - t).days, "err": pred - g["gl"].toordinal()})
    return rows


def _q(xs, p):
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * len(s)))] if s else 0


def forecast_report(today: date | None = None) -> dict:
    today = today or date.today()
    groups = release_groups()
    rows = backtest(groups, today)
    cutoffs = sorted({r["cutoff"] for r in rows})
    split = cutoffs[len(cutoffs) // 2]
    tune = [r for r in rows if r["cutoff"] < split]
    test = [r for r in rows if r["cutoff"] >= split]
    by = lambda rs, m: [r for r in rs if r["model"] == m]
    table = []
    for m in MODELS:
        a, b = [abs(r["err"]) for r in by(tune, m)], [abs(r["err"]) for r in by(test, m)]
        table.append({"model": m, "tune_mae": mean(a), "test_mae": mean(b), "test_median": median(b),
                      "all_mae": mean(abs(r["err"]) for r in by(rows, m)),
                      "test_bias": mean(r["err"] for r in by(test, m)), "test_p90": _q(b, 0.9)})
    best = min(table, key=lambda r: r["tune_mae"])["model"]  # chosen on the tuning half only
    q80 = round(_q([abs(r["err"]) for r in by(tune, best)], 0.8))  # split-conformal 80% interval half-width
    test_best = by(test, best)
    coverage = mean(abs(r["err"]) <= q80 for r in test_best)
    # the app's original interval: +/- max(7, 1.5 * residual SD of its own fit), evaluated on the same test rows
    v1_cov, v1_width = [], []
    for t in sorted({r["cutoff"] for r in test}):
        post = [g for g in groups if LAUNCH < g["gl"] <= t]
        pairs = [(g["jp"].toordinal(), g["gl"].toordinal()) for g in sorted(post, key=lambda g: g["gl"])[-30:]]
        a, b = ols(pairs)
        half = max(7, 1.5 * pstdev([y - (a + b * x) for x, y in pairs]))
        hits = [abs(r["err"]) <= half for r in test if r["cutoff"] == t and r["model"] == APP_V1]
        v1_cov += hits
        v1_width += [half] * len(hits)
    buckets = [(0, 30), (31, 60), (61, 90)]
    horizon = [{"ahead": f"{lo}-{hi} days", **{m: mean(abs(r["err"]) for r in by(test, m) if lo <= r["ahead"] <= hi)
                                            for m in (best, APP_V1, "fixed lag (median, baseline)")}}
               for lo, hi in buckets]
    # 95% CI for test MAE: block bootstrap over cutoffs (a cutoff's predictions are correlated, so resample whole cutoffs)
    rng = random.Random(11)
    test_cuts = sorted({r["cutoff"] for r in test})

    def boot(m):
        per = {t: [abs(r["err"]) for r in test if r["cutoff"] == t and r["model"] == m] for t in test_cuts}
        stats = []
        for _ in range(2000):
            errs = [e for t in (rng.choice(test_cuts) for _ in test_cuts) for e in per[t]]
            stats.append(mean(errs))
        return _q(stats, 0.025), _q(stats, 0.975)
    ci = {m: boot(m) for m in (best, "last lag (naive, baseline)")}
    distinct = len({r["jp"] for r in test_best})
    # after evaluation, pick what the app ships using every cutoff (standard refit-on-all-data step)
    deploy = min(table, key=lambda r: r["all_mae"])["model"]
    deploy_q80 = round(_q([abs(r["err"]) for r in by(rows, deploy)], 0.8))
    return {"ci": ci, "distinct": distinct, "test_cutoffs": len(test_cuts), "deploy": deploy, "deploy_q80": deploy_q80, "v1_width": mean(v1_width),
            "naive_mae": next(r["test_mae"] for r in table if r["model"].startswith("last lag")),
            "groups": len(groups), "post_launch": sum(g["gl"] > LAUNCH for g in groups), "cutoffs": len(cutoffs),
            "split": split, "first": cutoffs[0], "last": cutoffs[-1], "test_predictions": len(test_best),
            "table": table, "best": best, "q80": q80, "coverage": coverage, "v1_coverage": mean(v1_cov),
            "horizon": horizon, "baseline_mae": next(r["test_mae"] for r in table if r["model"].startswith("fixed")),
            "v1_mae": next(r["test_mae"] for r in table if r["model"] == APP_V1)}


def forecast_markdown(r: dict) -> str:
    best = next(x for x in r["table"] if x["model"] == r["best"])
    lines = [
        "## 1. Banner forecast backtest",
        "",
        "**Question:** how accurately can Global release dates be predicted from JP release history?",
        "",
        f"**Data:** {r['groups']} SSR banners (support + uma cards grouped by JP release date; GameTora). "
        f"The {r['groups'] - r['post_launch']} banners released together at Global launch ({LAUNCH}) are excluded "
        "from training, since that was a catalog dump, not a release decision.",
        "",
        f"**Method:** rolling-origin backtest. At each weekly cutoff from {r['first']} to {r['last']} "
        f"({r['cutoffs']} cutoffs), each model is fit only on releases known by that date and predicts every banner "
        f"that actually released in the next {HORIZON} days. Models are compared on the first half of cutoffs "
        f"(before {r['split']}); the winner is chosen there and reported on the second half, which it never saw. "
        "The 80% interval is split-conformal: the 80th percentile of the winner's absolute errors on the tuning half.",
        "",
        "| Model | Tuning MAE (days) | **Test MAE** | Test median | Test bias | Test 90th pct | All cutoffs MAE |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [f"| {x['model']}{' ✅' if x['model'] == r['best'] else ''} | {x['tune_mae']:.1f} | **{x['test_mae']:.1f}** | "
              f"{x['test_median']:.0f} | {x['test_bias']:+.1f} | {x['test_p90']:.0f} | {x['all_mae']:.1f} |" for x in r["table"]]
    lines += [
        "",
        f"**Result:** the model selected on the tuning half (*{r['best']}*) predicts release dates with a held-out "
        f"mean error of **{best['test_mae']:.1f} days** (95% CI {r['ci'][r['best']][0]:.1f}-{r['ci'][r['best']][1]:.1f}) vs "
        f"**{r['naive_mae']:.1f}** (95% CI {r['ci']['last lag (naive, baseline)'][0]:.1f}-{r['ci']['last lag (naive, baseline)'][1]:.1f}) "
        "for the strongest naive baseline (last observed lag, "
        f"{(1 - best['test_mae'] / r['naive_mae']) * 100:.0f}% lower) and {r['baseline_mae']:.1f} for a fixed lag, "
        "which fails because the JP→Global gap shrinks every month. Recency-windowed fits did slightly better on the "
        f"test half than the selected model ({r['v1_mae']:.1f} days for the app's original last-30 fit): Global's pace "
        "drifted after the tuning period, which a window adapts to.",
        "",
        f"**Sample size, honestly:** the test half has {r['test_predictions']} predictions, but they come from "
        f"{r['test_cutoffs']} overlapping weekly cutoffs about only **{r['distinct']} distinct banners**, so the "
        "effective sample is closer to the banner count. The CIs above resample whole cutoffs to respect that.",
        "",
        f"**Intervals:** a ±{r['q80']} day interval covered **{r['coverage'] * 100:.0f}%** of held-out releases "
        f"(target 80%). The app's original interval averaged ±{r['v1_width']:.1f} days and covered "
        f"{r['v1_coverage'] * 100:.0f}%: safe, but wider than it needed to be.",
        "",
        f"**Shipped in the app:** after testing, the model with the lowest error across all cutoffs, *{r['deploy']}*, "
        f"with a ±{r['deploy_q80']} day interval (80th percentile of its backtest errors). That interval uses every "
        "cutoff, including the ones used to choose the model, so treat its coverage as slightly optimistic; the "
        "held-out coverage above is the honest check. Conformal coverage also assumes the future release pattern "
        "resembles the past, which a publisher can change at will.",
        "",
        "Error by how far ahead the prediction was made (test half, MAE in days):",
        "",
        f"| Days ahead | {r['best']} | {APP_V1} (original) | Fixed lag |",
        "|---|---|---|---|",
    ]
    lines += [f"| {h['ahead']} | {h[r['best']]:.1f} | {h[APP_V1]:.1f} | {h['fixed lag (median, baseline)']:.1f} |" for h in r["horizon"]]
    lines += [
        "",
        "**Limitations:** predictions from neighbouring cutoffs overlap, so errors aren't independent and the "
        "effective sample is smaller than the prediction count. The Global schedule is set by the publisher and can "
        "change pace at any time (anniversaries, catch-up campaigns); a straight line can't anticipate that. "
        "Officially announced dates always override the forecast in the app.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- tier model validation

TIER_ORD = {"SS": 4, "S": 3, "A": 2, "B": 1, "C": 0}
STAT_TYPES = ("Speed", "Stamina", "Power", "Guts", "Wit")
# model constants the search may rescale (x0.5 to x2); ranking-irrelevant ones (e.g. stat caps) are left alone
TUNABLE = ["gain_main", "gain_sub", "per_support", "mood_bonus", "bond_per_train", "pick_early", "pick_rainbow",
           "pick_normal", "races", "race_gain", "hint_rate", "hint_sp", "hint_level_sp", "sp_to_stat",
           "energy_value", "fail_value", "event_energy", "event_stats"]


def tier_labels() -> list[dict]:
    p = ROOT / "data" / "tier_labels.json"
    if not p.exists():
        return []
    cards = gd.support_cards()
    return [{"card": cards[x["support_id"]], "tier": TIER_ORD[x["tier"]]}
            for x in json.loads(p.read_text("utf-8"))["labels"]
            if x["support_id"] in cards and cards[x["support_id"]]["type"] in STAT_TYPES]


def concordance(items: list[dict], score, keep=lambda a, b: True) -> float:
    """Share of same-type, different-tier pairs the score orders like the labels (ties count half)."""
    good = total = 0.0
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a["card"]["type"] != b["card"]["type"] or a["tier"] == b["tier"] or not keep(a, b):
                continue
            hi, lo = (a, b) if a["tier"] > b["tier"] else (b, a)
            d = score(hi) - score(lo)
            good += 1 if d > 0 else 0.5 if d == 0 else 0
            total += 1
    return good / total if total else float("nan")


def _scorer(w):
    import model
    pri = model.generic_priority(w)
    cache = {}

    def score(x):
        cid = x["card"]["id"]
        if cid not in cache:
            cache[cid] = model.card_value(x["card"], 4, pri, w)["total"]  # Game8 rates at MLB
        return cache[cid]
    return score




def _trial_weights(rng, spread: float = 1.0) -> dict:
    """Random weights: each tunable constant x 2^U(-spread, spread); probabilities capped at 0.95."""
    import model
    w = {k: model.WEIGHTS[k] * 2 ** rng.uniform(-spread, spread) for k in TUNABLE}
    return {k: min(v, 0.95) if k in model.PROBABILITIES else v for k, v in w.items()}


def tier_report(trials: int = 300, splits: int = 20, seed: int = 7, spread: float = 1.0) -> dict | None:
    import model
    items = tier_labels()
    if len(items) < 20:
        return None
    rng = random.Random(seed)
    recency = lambda x: x["card"]["release_gl"] or ""
    rarity_recency = lambda x: (x["card"]["rarity"], x["card"]["release_gl"] or "")
    as_num = lambda key: (lambda x: hash_rank[key][x["card"]["id"]])
    hash_rank = {}
    for name, key in (("recency", recency), ("rarity+recency", rarity_recency)):  # sort keys -> numbers, ties stay tied
        keys = sorted({key(x) for x in items})
        hash_rank[name] = {x["card"]["id"]: keys.index(key(x)) for x in items}
    default = _scorer(model.weights(calibrated=False))  # the hand-set prior
    shipped = _scorer(model.weights())
    candidates = [{}] + [_trial_weights(rng, spread) for _ in range(trials)]  # {} = current defaults
    scorers = [_scorer(model.weights(c, calibrated=False)) for c in candidates]
    by_type = {t: [x for x in items if x["card"]["type"] == t] for t in STAT_TYPES}
    held_default, held_tuned, held_recency, picked = [], [], [], []  # paired: same split for each
    for _ in range(splits):  # stratified 50/50 split of cards, per type
        train, test = [], []
        for xs in by_type.values():
            xs = xs[:]
            rng.shuffle(xs)
            train += xs[: len(xs) // 2]
            test += xs[len(xs) // 2:]
        best = max(range(len(candidates)), key=lambda i: concordance(train, scorers[i]))
        picked.append(best)
        held_tuned.append(concordance(test, scorers[best]))
        held_default.append(concordance(test, default))
        held_recency.append(concordance(test, as_num("recency")))
    full_best = max(range(len(candidates)), key=lambda i: concordance(items, scorers[i]))
    tuned_w = candidates[full_best]
    hand = model.weights(calibrated=False)
    special = {x["card"]["id"] for x in items if model.card_value(x["card"], 4, model.generic_priority(hand), hand)["notes"]}
    plain = lambda a, b: a["card"]["id"] not in special and b["card"]["id"] not in special
    # which cards the default model disagrees with most: rank within type vs label
    rows = []
    for t, xs in by_type.items():
        ranked = sorted(xs, key=default, reverse=True)
        for i, x in enumerate(ranked):
            label_rank = sum(y["tier"] > x["tier"] for y in xs)
            rows.append({"card": f"{x['card']['title']} {x['card']['name']}", "type": t, "special": x["card"]["id"] in special,
                         "tier": next(k for k, v in TIER_ORD.items() if v == x["tier"]),
                         "model_rank": i + 1, "label_rank": label_rank + 1, "of": len(xs), "gap": i - label_rank})
    sd = lambda xs: pstdev(xs) if len(xs) > 1 else 0
    return {
        "n": len(items), "types": {t: len(v) for t, v in by_type.items()},
        "tiers": {k: sum(x["tier"] == v for x in items) for k, v in TIER_ORD.items()},
        "pairs": sum(1 for t in by_type.values() for i, a in enumerate(t) for b in t[i + 1:] if a["tier"] != b["tier"]),
        "full": {"default": concordance(items, default), "recency": concordance(items, as_num("recency")),
                 "rarity+recency": concordance(items, as_num("rarity+recency")),
                 "tuned (in-sample)": concordance(items, scorers[full_best]),
                 "shipped (in-sample)": concordance(items, shipped)},
        "per_type": {t: {"default": concordance(xs, default), "recency": concordance(xs, as_num("recency"))}
                     for t, xs in by_type.items()},
        "cv": {"default": (mean(held_default), sd(held_default)), "tuned": (mean(held_tuned), sd(held_tuned)),
               "recency": (mean(held_recency), sd(held_recency)),
               "wins": sum(a > b for a, b in zip(held_tuned, held_default)), "splits": splits,
               "diff": (mean(a - b for a, b in zip(held_tuned, held_default)),
                        min(a - b for a, b in zip(held_tuned, held_default))),
               "chose_default": sum(p == 0 for p in picked)},
        "special": len(special),
        "error_split": {"plain": concordance(items, default, plain),
                        "special": concordance(items, default, lambda a, b: not plain(a, b)),
                        "top_under_special": sum(1 for x in sorted(rows, key=lambda r: -r["gap"])[:5] if x["special"])},
        "tuned_weights": {k: round(v / model.WEIGHTS[k], 2) for k, v in tuned_w.items()},
        "overrated": sorted(rows, key=lambda r: r["gap"])[:5], "underrated": sorted(rows, key=lambda r: -r["gap"])[:5],
        "trials": trials,
    }


def tier_markdown(r: dict) -> str:
    pct = lambda x: f"{x * 100:.1f}%"
    cv = r["cv"]
    lines = [
        "## 2. Support card model vs expert tier list",
        "",
        "**Question:** does the app's support-card score (an approximate training-contribution model built from each "
        "card's per-level effect table) rank cards the way experienced players do?",
        "",
        f"**Labels:** Game8's Global support card tier list (retrieved 2026-09-24; kept out of the public repo as "
        "third-party editorial content, so `data/tier_labels.json` must be regenerated locally to rerun this section): "
        f"{r['n']} Speed/Stamina/Power/Guts/Wit "
        f"cards rated {', '.join(f'{k} {v}' for k, v in r['tiers'].items())}. Game8 ranks cards *within* each type, "
        f"so every comparison is within type: {r['pairs']} card pairs of the same type and different tiers.",
        "",
        "**Metric:** pairwise concordance, the share of those pairs the score orders the same way as Game8 "
        "(50% = coin flip, 100% = perfect). Model scores are at max limit break, matching Game8's assumption.",
        "",
        "| Ranking | Concordance (all labeled cards) |",
        "|---|---|",
        f"| Newer card is better (baseline) | {pct(r['full']['recency'])} |",
        f"| SSR before SR, then newer (baseline) | {pct(r['full']['rarity+recency'])} |",
        f"| **App model, hand-set weights** | **{pct(r['full']['default'])}** |",
        f"| App model, shipped calibrated weights (fit on these same cards, so optimistic) | {pct(r['full']['shipped (in-sample)'])} |",
        "",
        "Per type (default weights vs recency baseline):",
        "",
        "| Type | Cards | Model | Recency |",
        "|---|---|---|---|",
    ]
    lines += [f"| {t} | {r['types'][t]} | {pct(v['default'])} | {pct(v['recency'])} |" for t, v in r["per_type"].items()]
    lines += [
        "",
        f"**Calibration (held out):** {r['trials']} random weight settings (each of {len(TUNABLE)} model constants "
        "scaled x0.5-x2) plus the defaults. For each of "
        f"{cv['splits']} stratified 50/50 splits of the cards, the best setting on the training half was scored on "
        "the held-out half. Probability-like constants are capped at 0.95.",
        "",
        "| On held-out cards | Mean concordance ± SD |",
        "|---|---|",
        f"| Recency baseline | {pct(cv['recency'][0])} ± {cv['recency'][1] * 100:.1f} |",
        f"| Hand-set weights | {pct(cv['default'][0])} ± {cv['default'][1] * 100:.1f} |",
        f"| Tuned on the other half | {pct(cv['tuned'][0])} ± {cv['tuned'][1] * 100:.1f} |",
        "",
        f"Tuned weights beat the hand-set ones on {cv['wins']} of {cv['splits']} held-out splits"
        + (f" (the search kept the defaults {cv['chose_default']} times)." if cv["chose_default"] else ".")
        + f" Paired improvement: +{cv['diff'][0] * 100:.1f} points on average, smallest +{cv['diff'][1] * 100:.1f}. "
        "The splits share cards, so they aren't independent: read the ± as spread across splits, not a confidence "
        "interval, and the 20/20 as consistency, not 20 separate confirmations.",
        "",
        "Largest disagreements (default model, rank within type vs Game8 rank):",
        "",
        "| Card | Type | Game8 tier | Model rank | Game8 rank |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {x['card']} | {x['type']} | {x['tier']} | {x['model_rank']}/{x['of']} | ~{x['label_rank']}/{x['of']} |"
              for x in r["overrated"] + r["underrated"]]
    es = r["error_split"]
    moved = sorted(r["tuned_weights"].items(), key=lambda kv: -abs(kv[1] - 1))[:6]
    lines += [
        "",
        f"**Error analysis:** {r['special']} of the {r['n']} cards have a *conditional* unique effect (e.g. a bonus "
        "that only applies under certain training conditions) that the model doesn't simulate yet. "
        f"{es['top_under_special']} of the 5 most-underrated cards are among them. On pairs of cards without one, the "
        f"model agrees with Game8 **{pct(es['plain'])}** of the time; on pairs involving one, only {pct(es['special'])}. "
        "Modeling those effects is the clearest next improvement.",
        "",
        "**Shipped:** the app now uses the setting fit on all labeled cards (`model.CALIBRATED`); the hand-set values "
        "remain in `model.WEIGHTS` as the starting point. The held-out numbers above are the honest estimate of how "
        "well it generalizes.",
        "",
        f"**Sensitivity:** widening the search to x0.25-x4 gives {pct(r['wide']['cv'])} held-out, but the fitted "
        f"weights change a lot (e.g. `gain_main` x{r['tuned_weights']['gain_main']} → x{r['wide']['weights']['gain_main']}). "
        "Many weight settings rank cards about equally well, so single weights aren't identifiable and "
        "shouldn't be interpreted. The gain is in the ranking, not in any one number.",
    ]
    lines += [
        "",
        "**Limitations:** one expert source; its tiers are coarse and tuned to the current scenario, while the model "
        "is scenario-agnostic. Within-type labels mean the model's cross-type ordering (e.g. Speed vs Wit) isn't "
        "tested here. Cards Game8 doesn't rate aren't evaluated.",
    ]
    return "\n".join(lines)


def write_report(fr: dict, tr: dict | None) -> Path:
    out = ROOT / "docs" / "EVALUATION.md"
    out.parent.mkdir(exist_ok=True)
    parts = ["# Model evaluation", "",
             f"Generated by `python evaluate.py` on {date.today()}. Every number below comes from that script; rerun it "
             "after a game data update to refresh.", "", forecast_markdown(fr)]
    if tr:
        parts += ["", tier_markdown(tr)]
    out.write_text("\n".join(parts) + "\n", "utf-8")
    return out


if __name__ == "__main__":
    fr = forecast_report()
    tr = tier_report()
    if tr:  # sensitivity check: same procedure, wider search range
        wide = tier_report(spread=2.0)
        tr["wide"] = {"cv": wide["cv"]["tuned"][0], "weights": wide["tuned_weights"]}
    print("wrote", write_report(fr, tr))
