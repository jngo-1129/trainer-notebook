"""Approximate race simulator + target stat calculator.

Safety: pure math on numbers the user types. No game access.

ESTIMATE ONLY. Uses community-documented race formulas (base speed, phase target speeds, last spurt,
acceleration, HP pool and consumption with guts in the final leg). It ignores position keeping, lanes,
blocking, slopes, randomness and most skills, so treat the output as "roughly how these stats compare",
not a finish-time prediction.
"""
from math import sqrt

STYLE_SPEED = {"front": (1.0, 0.98, 0.962), "pace": (0.978, 0.991, 0.975),
               "late": (0.938, 0.998, 0.994), "end": (0.931, 1.0, 1.0)}
STYLE_ACCEL = {"front": (1.0, 1.0, 0.996), "pace": (0.985, 1.0, 0.996),
               "late": (0.975, 1.0, 1.0), "end": (0.945, 1.0, 0.997)}
STYLE_HP = {"front": 0.95, "pace": 0.89, "late": 1.0, "end": 0.995}
DIST_APT = {"S": 1.05, "A": 1.0, "B": 0.9, "C": 0.8, "D": 0.6, "E": 0.4, "F": 0.2, "G": 0.1}
SURF_APT = {"S": 1.05, "A": 1.0, "B": 0.9, "C": 0.8, "D": 0.7, "E": 0.5, "F": 0.3, "G": 0.1}
MOOD = [0.96, 0.98, 1.0, 1.02, 1.04]  # awful..great
LENGTH_M = 2.5


def simulate(stats: dict, distance: int, style: str = "pace", dist_apt: str = "A", surf_apt: str = "A",
             mood: int = 4, heal_pct: float = 0.0, dt: float = 0.05) -> dict:
    """Run one idealized race. heal_pct = total HP recovered by skills, as % of max HP."""
    m = MOOD[mood]
    spd, sta, pw, gut = (stats.get(k, 0) * m for k in ("speed", "stamina", "power", "guts"))
    base = 20 - (distance - 2000) / 1000
    da = DIST_APT[dist_apt]
    stat_speed = sqrt(500 * spd) * da * 0.002
    target = [base * STYLE_SPEED[style][0], base * STYLE_SPEED[style][1], base * STYLE_SPEED[style][2] + stat_speed]
    spurt_max = (base * (STYLE_SPEED[style][2] + 0.01) + stat_speed) * 1.05 + stat_speed + (450 * gut) ** 0.597 * 0.0001
    min_speed = 0.85 * base + sqrt(200 * gut) * 0.001
    accel = lambda ph: 0.0006 * sqrt(500 * pw) * STYLE_ACCEL[style][min(ph, 2)] * SURF_APT[surf_apt]
    max_hp = 0.8 * STYLE_HP[style] * sta + distance
    hp = max_hp * (1 + heal_pct / 100)
    guts_mod = 1 + 200 / sqrt(600 * max(gut, 1))
    burn = lambda v, late: 20 * (v - base + 12) ** 2 / 144 * (guts_mod if late else 1)

    pos, v, t, spurt, spurt_ok, out_of_hp = 0.0, 3.0, 0.0, None, True, False
    while pos < distance and t < 600:
        ph = 0 if pos < distance / 6 else 1 if pos < distance * 2 / 3 else 2
        if ph == 2 and spurt is None:  # decide the last spurt speed once, at the start of the final leg
            remaining = distance - pos
            options = [spurt_max] + [s / 10 for s in range(int(spurt_max * 10), int(target[2] * 10) - 1, -1)]
            spurt = next((s for s in options if hp >= remaining / s * burn(s, True)), target[2])
            spurt_ok = spurt == spurt_max
        goal = min_speed if hp <= 0 else (spurt if ph == 2 else target[ph])
        if hp <= 0:
            out_of_hp = True
        if v < goal:
            a = accel(ph) + (24 if ph == 0 and v < 0.85 * base else 0)  # start dash
            v = min(goal, v + a * dt)
        else:
            v = max(goal, v - (0.8 if ph == 0 else 1.0 if ph == 1 else 1.2) * dt)
        hp -= burn(v, ph == 2) * dt
        pos += v * dt
        t += dt
    t -= (pos - distance) / max(v, 0.1)  # interpolate the finish inside the last step
    # time = raw simulation seconds (the in-game clock shows a scaled time)
    return {"time": round(t, 3), "hp_left": round(max(hp, 0)), "max_hp": round(max_hp), "spurt_ok": spurt_ok,
            "out_of_hp": out_of_hp, "spurt_speed": round(spurt or 0, 2), "spurt_max": round(spurt_max, 2),
            "end_speed": round(v, 2)}


def compare(mine: dict, field: dict, distance: int, style: str, skill_lengths: float = 0.0, **kw) -> dict:
    """Me vs a typical meta runner. Positive lengths = ahead."""
    a, b = simulate(mine, distance, style, **kw), simulate(field, distance, style)
    lengths = (b["time"] - a["time"]) * a["end_speed"] / LENGTH_M + skill_lengths
    return {"me": a, "field": b, "lengths": round(lengths, 1),
            "verdict": "ahead of" if lengths > 0.5 else "behind" if lengths < -0.5 else "about even with"}


def stamina_needed(stats: dict, distance: int, style: str = "pace", heal_pct: float = 0.0, **kw) -> int:
    """Lowest stamina that still allows a full last spurt (binary search)."""
    lo, hi = 100, 2000
    if not simulate(stats | {"stamina": hi}, distance, style, heal_pct=heal_pct, **kw)["spurt_ok"]:
        return hi
    while hi - lo > 10:
        mid = (lo + hi) // 2
        if simulate(stats | {"stamina": mid}, distance, style, heal_pct=heal_pct, **kw)["spurt_ok"]:
            hi = mid
        else:
            lo = mid
    return hi


def targets(distance: int, style: str, base_targets: dict, heal_pct: float = 0.0) -> dict:
    """Target stats: distance defaults, with stamina replaced by what the sim needs for a full spurt."""
    t = dict(base_targets)
    t["stamina"] = stamina_needed(t, distance, style, heal_pct)
    return t


def _selftest():
    s = {"speed": 1100, "stamina": 800, "power": 900, "guts": 400, "wit": 600}
    r = simulate(s, 2000)
    assert 90 < r["time"] < 110, r  # raw sim seconds; the game displays a scaled-up time
    assert simulate(s | {"speed": 1200, "stamina": 1200}, 2000)["time"] < simulate(s | {"stamina": 1200}, 2000)["time"]
    assert abs(simulate(s | {"speed": 1200}, 2000)["time"] - r["time"]) < 0.05  # HP-limited: extra speed is wasted
    assert simulate(s | {"stamina": 300}, 3200)["spurt_ok"] is False
    need = stamina_needed(s, 2400)
    assert simulate(s | {"stamina": need}, 2400)["spurt_ok"] and 300 < need < 1600, need
    assert stamina_needed(s, 2400, heal_pct=10) < need  # recovery skills cut the requirement
    c = compare(s | {"speed": 1200, "stamina": 1200}, s | {"stamina": 1200}, 2000, "pace")
    assert c["lengths"] > 0 and c["verdict"] == "ahead of"
    print("racesim selftest: PASS")


if __name__ == "__main__":
    _selftest()
