"""Screenshot importer: OCR the user's own screenshots and suggest matches from the game DB.

Safety: reads only image files in the user-chosen screenshot folder (default ./screenshots).
Never captures the screen, never looks at the game window, process, or install folder (rules 1, 4, 7a).
OCR (RapidOCR/onnxruntime) runs fully offline; models ship inside the pip wheel.

Nothing is saved automatically: results are suggestions the user confirms in the editor.
ponytail: text-only matching. Limit-break pips, star icons, and card-art-only list screens aren't read yet;
add image template matching once there are sample screenshots in tests/screenshots/ to tune against.

CLI (used by the ocr-tester agent):  python importer.py <image> [<image> ...]   -> JSON per image
"""
import json
import re
import sys
from difflib import SequenceMatcher, get_close_matches
from functools import cache
from pathlib import Path

import gamedata

DEFAULT_DIR = Path(__file__).parent / "screenshots"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
NUM_RE = re.compile(r"\d[\d,]*")


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def list_images(folder: Path) -> list[Path]:
    return sorted((p for p in folder.glob("*") if p.suffix.lower() in IMAGE_EXT), key=lambda p: p.stat().st_mtime)


@cache
def _engine():
    from rapidocr_onnxruntime import RapidOCR  # slow import, load on first use
    return RapidOCR()


@cache
def vocab() -> dict[str, list[dict]]:
    """normalized text -> things it could be."""
    v: dict[str, list[dict]] = {}
    add = lambda text, item: v.setdefault(norm(text), []).append(item) if norm(text) else None
    for kind, cards in (("support", gamedata.support_cards()), ("uma", gamedata.uma_cards())):
        for c in cards.values():
            add(c["title"], {"kind": kind, "id": c["id"], "label": f"{c['title']} {c['name']}"})
    for name in {c["name"] for c in gamedata.uma_cards().values()} | {c["name"] for c in gamedata.support_cards().values()}:
        add(name, {"kind": "character", "name": name, "label": name})
    for color, names in gamedata.spark_names().items():
        for n in names:
            add(n, {"kind": "spark", "color": color, "name": n, "label": f"{color} spark: {n}"})
    return v


def rows(ocr_result) -> list[str]:
    """Group OCR word boxes into text lines by vertical position, left to right."""
    boxes = sorted(((b[0][1] + b[2][1]) / 2, b[2][1] - b[0][1], b[0][0], t) for b, t, _ in ocr_result or [])
    lines: list[list] = []
    for yc, h, x, t in boxes:
        if lines and abs(yc - lines[-1][0]) < h / 2:
            lines[-1][1].append((x, t))
        else:
            lines.append([yc, [(x, t)]])
    return [" ".join(t.strip() for _, t in sorted(words)) for _, words in lines]


def match(lines: list[str]) -> dict:
    v = vocab()
    keys = list(v)
    matches, numbers, seen = [], [], set()
    for line in lines:
        n = norm(line)
        hits = get_close_matches(n, keys, n=3, cutoff=0.8) if n else []
        hits += [k for k in keys if len(k) >= 8 and k in n and k not in hits]  # title embedded in a longer line
        for k in hits:
            for item in v[k]:
                key = json.dumps(item, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    matches.append(item | {"line": line, "score": round(SequenceMatcher(None, n, k).ratio(), 2)})
        if nums := NUM_RE.findall(line):
            label = NUM_RE.sub("", line).strip(" :x×")
            numbers += [{"label": label, "value": int(x.replace(",", "")), "line": line} for x in nums]
    matches.sort(key=lambda m: -m["score"])
    return {"lines": lines, "matches": matches, "numbers": numbers}


def read(path: Path) -> dict:
    result, _ = _engine()(str(path))
    return {"file": path.name} | match(rows(result))


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(json.dumps(read(Path(arg)), ensure_ascii=False, indent=1))
