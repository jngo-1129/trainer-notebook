"""Game database: public GameTora JSON, cached in data/cache/.

Safety: anonymous HTTPS GET to gametora.com and one public GitHub repo (UmaTools, for English event
choices and career objectives) only (SAFETY.md allow-list). No cookies, no auth, never contacts game servers.
Local cache means the app works offline after one update.
"""
import json
import urllib.request
from datetime import date
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

CACHE = Path(__file__).parent / "data" / "cache"
BASE = "https://gametora.com/data"
UMATOOLS = "https://raw.githubusercontent.com/daftuyda/UmaTools/HEAD/public/assets"
# cache name -> GameTora manifest key
DATASETS = {"support-cards": "support-cards", "character-cards": "character-cards", "characters": "characters",
            "skills": "skills", "factors": "factors", "races": "races", "race_instances": "race_instances",
            "racetracks": "racetracks", "relation": "db-files/succession_relation",
            "relation_member": "db-files/succession_relation_member", "cm_global": "en/events/champions-meeting",
            "cm_jp": "events/champions-meeting"}
UMATOOLS_SETS = {"ut_uma": "uma_data.json", "ut_support_events": "support_card.json", "ut_career": "career.json"}
TRACKS = {10001: "Sapporo", 10002: "Hakodate", 10003: "Niigata", 10004: "Fukushima", 10005: "Nakayama",
          10006: "Tokyo", 10007: "Chukyo", 10008: "Kyoto", 10009: "Hanshin", 10010: "Kokura", 10101: "Oi",
          10103: "Kawasaki", 10104: "Funabashi", 10105: "Morioka", 10201: "Longchamp", 10202: "Santa Anita",
          10203: "Del Mar"}
APT_KEYS = ["turf", "dirt", "short", "mile", "medium", "long", "front", "pace", "late", "end"]
STATS = ["speed", "stamina", "power", "guts", "wit"]
STAT_TYPES = {"speed": "Speed", "stamina": "Stamina", "power": "Power", "guts": "Guts",
              "intelligence": "Wit", "friend": "Friend", "group": "Group"}


def allowed(url: str) -> bool:
    """Exact allow-list: gametora.com (any subdomain) or the one UmaTools repo on GitHub raw, https only."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme != "https":
        return False
    if host == "gametora.com" or host.endswith(".gametora.com"):
        return True
    return host == "raw.githubusercontent.com" and u.path.startswith("/daftuyda/UmaTools/")


class _GuardRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):  # refuse before any request goes to the new host
            raise ValueError(f"refusing redirect off the allow-list: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# no ProxyHandler({}) means system proxy env vars can't reroute; no cookie processor at all
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _GuardRedirects)


def _get(url: str):
    if not allowed(url):
        raise ValueError(f"not on the allow-list: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "TrainerNotebook/0.1 (personal offline advisor)"})
    with _OPENER.open(req, timeout=60) as r:
        return json.load(r)


def update() -> dict:
    """Download every dataset, then swap it in. Returns {dataset: record count}."""
    manifest = _get(f"{BASE}/manifests/umamusume.json")
    CACHE.mkdir(parents=True, exist_ok=True)
    urls = {name: f"{BASE}/umamusume/{key}.{manifest[key]}.json" for name, key in DATASETS.items()}
    urls |= {name: f"{UMATOOLS}/{file}" for name, file in UMATOOLS_SETS.items()}
    fetched = {name: _get(url) for name, url in urls.items()}  # all or nothing: fail before writing
    counts = {}
    for name, data in fetched.items():
        (CACHE / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        counts[name] = len(data)
    meta = {"source": "https://gametora.com/umamusume + github.com/daftuyda/UmaTools",
            "retrieved": date.today().isoformat(), "counts": counts}
    (CACHE / "meta.json").write_text(json.dumps(meta), "utf-8")
    for f in CACHED:
        f.cache_clear()
    return counts


@cache
def curated() -> dict:
    """data/meta.json: community tiers, banners, CM cups, income, researched from public sites (committed, cited)."""
    p = CACHE.parent / "meta.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def meta() -> dict | None:
    p = CACHE / "meta.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else None


@cache
def _raw(name: str):
    p = CACHE / f"{name}.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else []


@cache
def support_cards() -> dict[int, dict]:
    return {c["support_id"]: {
        "id": c["support_id"], "name": c["char_name"], "title": c.get("title_en") or c.get("title_ja", ""),
        "type": STAT_TYPES.get(c["type"], c["type"]), "rarity": int(c["rarity"]),
        "name_jp": c.get("name_jp", ""), "title_jp": c.get("title_ja", ""),
        "release_jp": c.get("release"), "release_gl": c.get("release_en"), "char_id": int(c["char_id"]),
        "effects": c.get("effects", []), "unique": c.get("unique"),
        "hint_skills": (c.get("hints") or {}).get("hint_skills", []), "event_skills": c.get("event_skills", []),
    } for c in _raw("support-cards")}


def on_global(c: dict, today: str | None = None) -> bool:
    return bool(c.get("release_gl")) and c["release_gl"] <= (today or date.today().isoformat())


@cache
def uma_cards() -> dict[int, dict]:
    return {c["card_id"]: {
        "id": c["card_id"], "name": c["name_en"], "title": c.get("title_en_gl") or c.get("title", ""),
        "name_jp": c.get("name_jp", ""), "title_jp": c.get("title_jp", ""),
        "rarity": int(c["rarity"]), "release_jp": c.get("release"), "release_gl": c.get("release_en"),
        "unique_skill": (c.get("skills_unique") or [None])[0], "char_id": int(c["char_id"]),
        "apt": dict(zip(APT_KEYS, c.get("aptitude") or ["G"] * 10)),
        "growth": dict(zip(STATS, c.get("stat_bonus") or [0] * 5)),
        "base": dict(zip(STATS, c.get("base_stats") or [0] * 5)),
        "skills": c.get("skills_event", []) + c.get("skills_awakening", []),
    } for c in _raw("character-cards")}


@cache
def skills() -> dict[int, dict]:
    return {int(s["id"]): s for s in _raw("skills")}


@cache
def skill_names() -> dict[int, str]:
    return {i: s.get("name_en") or s.get("enname") or s["jpname"] for i, s in skills().items()}


@cache
def affinity_groups() -> dict[int, set[int]]:
    """char_id -> set of relation types (shared types add affinity points)."""
    out: dict[int, set[int]] = {}
    for m in _raw("relation_member"):
        out.setdefault(int(m["chara_id"]), set()).add(int(m["relation_type"]))
    return out


@cache
def relation_points() -> dict[int, int]:
    return {int(r["relation_type"]): int(r["relation_point"]) for r in _raw("relation")}


def affinity(*chars: int) -> int:
    """Community-documented compatibility: sum of relation points for groups every given character is in."""
    groups = set.intersection(*(affinity_groups().get(c, set()) for c in chars))
    pts = relation_points()
    return sum(pts.get(g, 0) for g in groups)


@cache
def objectives() -> dict[int, list[dict]]:
    """uma card id -> career objectives (from UmaTools)."""
    return {int(u["UmaId"]): u.get("UmaObjectives") or [] for u in _raw("ut_uma") if str(u.get("UmaId", "")).isdigit()}


@cache
def events() -> list[dict]:
    """English training events: {name, source, options: {label: effect text}}."""
    out = [{"name": e["EventName"], "source": f"{u['UmaName']} ({u['UmaNickname']})", "options": e["EventOptions"]}
           for u in _raw("ut_uma") for e in u.get("UmaEvents") or []]
    out += [{"name": e["EventName"], "source": "Support card", "options": e["EventOptions"]} for e in _raw("ut_support_events")]
    out += [{"name": e["EventName"], "source": "Career", "options": e["EventOptions"]} for e in _raw("ut_career")]
    return out


@cache
def spark_names() -> dict[str, list[str]]:
    """Spark (factor) names by color: blue=stat, pink=aptitude, green=unique skill, white=skill/race/scenario."""
    f = _raw("factors") or {}
    names = lambda key: sorted({x.get("name_en_gl") or x.get("name_en") for x in f.get(key, [])} - {None})
    skills = skill_names()
    green = sorted({skills[u["unique_skill"]] for u in uma_cards().values() if u["unique_skill"] in skills})
    return {"blue": names("blue"), "pink": names("pink"), "green": green,
            "white": sorted(set(names("skill") + names("race") + names("scenario")))}


CACHED = (_raw, curated, support_cards, uma_cards, skills, skill_names, spark_names, affinity_groups, relation_points,
          objectives, events)


def label(kind: str, cid: int) -> str:
    c = (support_cards() if kind == "support" else uma_cards()).get(cid)
    return f"{c['title']} {c['name']}" if c else f"#{cid}"


if __name__ == "__main__":
    print(update())
    print({k: len(v) for k, v in spark_names().items()}, len(support_cards()), len(uma_cards()))
