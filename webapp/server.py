#!/usr/bin/env python3
"""Minimal Champions VGC teambuilder UI.

Serves the static type-chart-style page and a JSON API that wraps
artifacts/team_recs.py. No extra Python packages required.

  cd artifacts/webapp
  python3 server.py
  # open http://127.0.0.1:8765
"""

from __future__ import annotations

import base64
import datetime
import inspect
import time
import io
import json
import re
import sys
import threading
import traceback
import urllib.request
from difflib import SequenceMatcher
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = HERE / "static"
sys.path.insert(0, str(ROOT))

import team_recs as recs  # noqa: E402


STATE = {
    "ready": False,
    "error": None,
    "corpus": None,
    "pastes": None,
    "names": [],
    "teams": 0,
    "weight": 0.0,
}
LOCK = threading.Lock()

# Official-ish type chart colors, lightened so body text stays readable.
TYPE_COLORS = {
    "Normal": "#C6C6A8",
    "Fire": "#F0A060",
    "Water": "#86B4F4",
    "Electric": "#F8DE6A",
    "Grass": "#96D070",
    "Ice": "#B4E4E0",
    "Fighting": "#D06058",
    "Poison": "#C070C0",
    "Ground": "#E8CF86",
    "Flying": "#C0B0F6",
    "Psychic": "#F880A8",
    "Bug": "#C4D050",
    "Rock": "#D0C068",
    "Ghost": "#8A70B0",
    "Dragon": "#8A62F8",
    "Dark": "#8A7468",
    "Steel": "#D0D0E0",
    "Fairy": "#E8A8C8",
    "Stellar": "#70C8C0",
}

_DEX_LOCK = threading.Lock()
_DEX: dict[str, list[str]] | None = None


def _dex_key(name: str) -> str:
    s = (name or "").strip().lower()
    s = s.replace("’", "'").replace(".", "")
    s = re.sub(r"^mega\s+", "", s)
    s = re.sub(r"\s+mega(?:\s+([xyz]))?$", lambda m: "mega" + (m.group(1) or ""), s)
    s = s.replace("-", "").replace(" ", "").replace("'", "")
    return s


def _load_showdown_dex() -> dict[str, list[str]]:
    url = "https://play.pokemonshowdown.com/data/pokedex.json"
    req = urllib.request.Request(url, headers={"User-Agent": "ChampionsVGCTeamRecs/ui"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.load(resp)
    out: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        types = [str(t).title() for t in (entry.get("types") or []) if t]
        if not types:
            continue
        names = {str(key).lower()}
        nm = entry.get("name")
        if nm:
            names.add(_dex_key(str(nm)))
        for n in names:
            out[n] = types
    return out


def _pokeapi_types(slug: str) -> list[str]:
    url = f"https://pokeapi.co/api/v2/pokemon/{slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "ChampionsVGCTeamRecs/ui"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.load(resp)
    except Exception:
        return []
    slots = sorted(payload.get("types") or [], key=lambda r: r.get("slot") or 0)
    return [str(r.get("type", {}).get("name", "")).title() for r in slots if r.get("type")]


def pokemon_types(name: str) -> list[str]:
    """Best-effort types for a Champions species name."""
    global _DEX
    with _DEX_LOCK:
        if _DEX is None:
            try:
                _DEX = _load_showdown_dex()
            except Exception:
                _DEX = {}
        dex = _DEX

    raw = name or ""
    keys = [_dex_key(raw)]
    stripped = re.sub(r"-mega(?:-[xyz])?$", "", raw, flags=re.I)
    stripped = re.sub(r"^mega\s+", "", stripped, flags=re.I)
    if stripped != raw:
        keys.append(_dex_key(stripped))
    # Hisuian / Paldean leftovers: try the last hyphen chunk dropped.
    if "-" in raw:
        keys.append(_dex_key(raw.split("-")[0]))

    for key in keys:
        if key in dex:
            return dex[key]

    slugs = [
        raw.strip().lower().replace(" ", "-").replace(".", "").replace("'", ""),
        stripped.strip().lower().replace(" ", "-").replace(".", "").replace("'", ""),
    ]
    for slug in slugs:
        if not slug:
            continue
        found = _pokeapi_types(slug)
        if found:
            with _DEX_LOCK:
                _DEX[keys[0]] = found
            return found
    return []


def type_colors(types: list[str]) -> list[str]:
    return [TYPE_COLORS.get(t, "#D8D8D8") for t in types]


def _pct(value) -> str | None:
    if value is None:
        return None
    return f"{value:.1%}"


REFRESH_SECONDS = 6 * 60 * 60
STALE_SNAPSHOTS = (
    "pika_usage_regmc.json",
    "limitless_regmc.json",
    "official_mc_2027.json",
)


def _drop_stale_snapshots() -> None:
    """Force the next load to pull usage, cups, and new official events."""
    cache = Path(__file__).resolve().parent.parent / "data" / "cache"
    for name in STALE_SNAPSHOTS:
        path = cache / name
        if path.is_file():
            path.unlink()


def _load(*, refresh: bool = False) -> None:
    try:
        if refresh:
            _drop_stale_snapshots()
        corpus, pastes = recs.load_teambuilder_data(
            include_worlds=True,
            include_official_season=False,
            include_official_mc=True,
            include_limitless=True,
            include_usage=True,
            include_sample_pastes=True,
        )
        corpus._ensure_index()
        names = sorted({disp for disp in corpus._by_key.values()}, key=str.casefold)
        with LOCK:
            STATE.update(
                ready=True,
                error=None,
                corpus=corpus,
                pastes=pastes,
                names=names,
                teams=len(corpus),
                weight=corpus.total_weight,
                updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
    except Exception as exc:
        with LOCK:
            STATE["ready"] = False
            STATE["error"] = f"{type(exc).__name__}: {exc}"


def _refresh_loop() -> None:
    _load(refresh=False)
    while True:
        time.sleep(REFRESH_SECONDS)
        _load(refresh=True)


def _explain(resolved, partners, sets, team, max_megas: int) -> dict:
    """Plain-language rationale for the core, partners, and filled slots."""
    points = []
    names = [r["canonical"] for r in resolved if r.get("canonical")]
    seed_n = len(names)
    slots = (team or {}).get("slots") or []
    added = [s["species"] for s in slots[seed_n:]]

    renamed = []
    for r in resolved:
        q, c, kind = r.get("query") or "", r.get("canonical") or "", r.get("kind") or ""
        if c and q and q.casefold() != c.casefold():
            if "mega" in kind:
                renamed.append(f"{q} → {c} (that is the forme this format actually plays)")
            else:
                renamed.append(f"{q} → {c}")
    if renamed:
        points.append({"title": "Name resolution", "text": "; ".join(renamed) + "."})

    if seed_n >= 6:
        points.append(
            {
                "title": "Full team entered",
                "text": "You already gave six Pokémon, so nothing was added. Partners below are alternatives that historically sit next to this core.",
            }
        )
    elif added:
        cap = "no mega cap" if not max_megas else f"at most {max_megas} mega slot{'s' if max_megas != 1 else ''}"
        points.append(
            {
                "title": "How the Bring-6 was filled",
                "text": (
                    f"Locked your {seed_n} pick{'s' if seed_n != 1 else ''} and added "
                    + ", ".join(added)
                    + f". Auto-picks prefer partners that show up with the well-supported members of the core, with {cap}."
                ),
            }
        )
    else:
        points.append(
            {
                "title": "No auto-fills",
                "text": "Nothing in the corpus had enough sample to grow this core to six. Sets below fall back to each mon's global usage.",
            }
        )

    points.append(
        {
            "title": "What the scores mean",
            "text": (
                "Lift is how much more often a partner appears with your core than its baseline usage "
                "(1.0 = average, 2.0 = twice as common next to you). Together is the raw co-occurrence rate. "
                "Anchors is the share of your well-supported core members that actually pair with it. "
                "WR is the winrate of those pairs, when we have games."
            ),
        }
    )

    for p in (partners or [])[:4]:
        voters = p.get("voters") or []
        voter_txt = ", ".join(voters[:4]) if voters else "the core"
        bits = [
            f"lift {p.get('avg_lift')}",
            f"together {p.get('avg_together_pct') or '—'}",
        ]
        if p.get("anchor_coverage_pct"):
            bits.append(f"anchors {p['anchor_coverage_pct']}")
        if p.get("pair_winrate_pct"):
            bits.append(f"pair WR {p['pair_winrate_pct']}")
        lift = p.get("avg_lift") or 0
        why = "above-baseline pairing" if lift >= 1.25 else "common in the format and compatible with an anchor"
        points.append(
            {
                "title": p.get("pokemon") or "Partner",
                "pokemon": p.get("pokemon"),
                "text": f"Suggested because of {why} with {voter_txt} ({'; '.join(bits)}).",
            }
        )

    fallbacks = [s for s in (sets or []) if s.get("fallback")]
    sliced = [s for s in (sets or []) if not s.get("fallback") and s.get("n_teams")]
    if sliced:
        bits = [f"{s['species']} from {int(round(s['n_teams']))} teams with this core" for s in sliced[:4]]
        points.append({"title": "Sets", "text": "Items and moves use the pair slice when it exists: " + "; ".join(bits) + "."})
    if fallbacks:
        points.append(
            {
                "title": "Thin sample",
                "text": ", ".join(s["species"] for s in fallbacks)
                + " did not have a large enough core-specific slice, so those sets are global usage for that Pokémon.",
            }
        )

    core = " + ".join(names) if names else "this core"
    summary = f"Recommendations for {core} come from Limitless / Worlds teamlists: partners that over-index with your anchors, then sets from those same slices."
    return {"summary": summary, "points": points}


_UNBURDEN_SPECIES = {"sneasler", "hawlucha", "slurpuff"}
_UNBURDEN_ITEMS = {
    "white herb",
    "power herb",
    "mental herb",
    "mirror herb",
    "focus sash",
    "air balloon",
    "weakness policy",
}


def _prefer_unburden(slot) -> None:
    """A consumed item does nothing for Poison Touch. Unburden is the point."""
    species = (slot.species or "").casefold().replace(" ", "").split("-")[0]
    if species not in _UNBURDEN_SPECIES:
        return
    item = (slot.item or "").casefold()
    consumed = item.endswith(" seed") or item.endswith(" berry") or item in _UNBURDEN_ITEMS
    if not consumed or (slot.ability or "").casefold() == "unburden":
        return
    old = slot.ability or "no ability"
    slot.ability = "Unburden"
    note = f"{old} → Unburden ({slot.item} is consumed)"
    if note not in slot.notes:
        slot.notes.append(note)


def _build_team(corpus, canonical, pastes, max_megas: int, locked_moves=None, roster=None):
    """Call build_team with max_megas when present, else the old one_mega flag."""
    params = inspect.signature(recs.build_team).parameters
    kwargs = {"pastes": pastes}
    if "max_megas" in params:
        kwargs["max_megas"] = max_megas
    elif "one_mega" in params:
        kwargs["one_mega"] = max_megas == 1
    if locked_moves and "locked_moves" in params:
        kwargs["locked_moves"] = locked_moves
    if roster and "roster" in params:
        kwargs["roster"] = roster
    return recs.build_team(corpus, canonical, **kwargs)


def _match_roster_text(text: str, names: list[str]) -> list[str]:
    cleaned = re.sub(r"[^A-Za-z0-9 \-\n]", " ", text or "")
    tokens = [t for t in re.split(r"[\s,;/|]+", cleaned) if len(t) >= 4]
    lines = [" ".join(line.split()) for line in cleaned.splitlines() if line.strip()]
    candidates = lines + tokens
    # Two-word windows catch "Indeedee F" and "Mega Garchomp".
    words = cleaned.split()
    candidates += [" ".join(words[i : i + 2]) for i in range(len(words) - 1)]
    found: list[str] = []
    seen = set()
    indexed = [(n, recs._norm(n)) for n in names]
    for raw in candidates:
        key = recs._norm(raw)
        if len(key) < 4:
            continue
        best = None
        best_score = 0.0
        for display, norm in indexed:
            if key == norm or key in norm or norm in key:
                score = 1.0 if key == norm else 0.9
            else:
                if abs(len(key) - len(norm)) > 4:
                    continue
                score = SequenceMatcher(None, key, norm).ratio()
            if score > best_score:
                best, best_score = display, score
        if best and best_score >= 0.84 and best.casefold() not in seen:
            seen.add(best.casefold())
            found.append(best)
    return found


def _decode_images(images: list):
    from PIL import Image
    out = []
    for item in images[:8]:
        raw = item.get("data") or item.get("image") if isinstance(item, dict) else item
        raw = str(raw or "")
        if "," in raw and raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            blob = base64.b64decode(raw)
            out.append(Image.open(io.BytesIO(blob)).convert("RGB"))
        except Exception:
            continue
    return out


def _extract_box_icons(img) -> list[dict]:
    """Pull Champions box cells. Names are not printed on this screen."""
    from PIL import Image
    work = img.copy()
    work.thumbnail((1100, 1100))
    w, h = work.size
    px = work.load()
    mask = [[False] * w for _ in range(h)]
    for y in range(h):
        row = mask[y]
        for x in range(w):
            r, g, b = px[x, y]
            if r > 214 and g > 214 and b > 220 and abs(r - g) < 22:
                row[x] = True
    seen = [[False] * w for _ in range(h)]
    boxes = []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if not mask[y][x] or seen[y][x]:
                continue
            stack = [(x, y)]
            seen[y][x] = True
            minx = maxx = x
            miny = maxy = y
            n = 0
            while stack:
                cx, cy = stack.pop()
                n += 1
                minx, maxx = min(minx, cx), max(maxx, cx)
                miny, maxy = min(miny, cy), max(maxy, cy)
                for nx, ny in ((cx - 2, cy), (cx + 2, cy), (cx, cy - 2), (cx, cy + 2)):
                    if 0 <= nx < w and 0 <= ny < h and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            bw, bh = maxx - minx + 1, maxy - miny + 1
            if n > 220 and 0.7 <= bw / max(bh, 1) <= 1.4 and 34 < bw < 150:
                boxes.append((minx, miny, maxx, maxy))
    icons = []
    for minx, miny, maxx, maxy in boxes:
        cell = work.crop((minx, miny, maxx + 1, maxy + 1))
        colors = list(cell.resize((24, 24)).getdata())
        reds = sum(1 for r, g, b in colors if r > 190 and r > g + 70 and r > b + 70)
        # Ban icon is a red circle-slash. Orange sprites stay under this count.
        if reds > 80:
            continue
        variance = 0
        avg = tuple(sum(c[i] for c in colors) / len(colors) for i in range(3))
        variance = sum(sum((c[i] - avg[i]) ** 2 for i in range(3)) for c in colors) / len(colors)
        if variance < 400:
            continue
        thumb = cell.resize((72, 72))
        buf = io.BytesIO()
        thumb.save(buf, format="PNG")
        icons.append(
            {
                "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
            }
        )
        if len(icons) >= 48:
            break
    return icons


def _scan_images(images: list, names: list[str]) -> tuple[list[str], str, list[dict]]:
    pictures = _decode_images(images)
    icons: list[dict] = []
    for img in pictures:
        icons.extend(_extract_box_icons(img))
    texts = []
    try:
        import pytesseract
        for img in pictures:
            try:
                texts.append(pytesseract.image_to_string(img))
            except Exception:
                texts.append("")
    except ImportError:
        pass
    text = "\n".join(texts)
    note = ""
    matched = _match_roster_text(text, names)
    if icons and not matched:
        note = f"Found {len(icons)} box icons. This screen has no names, so label the sprites below. Red-slash locks were skipped."
    elif not icons and not matched:
        note = "No names or box icons found. Crop to the Champions box and try again, or paste names."
    return matched, note or text[:400], icons[:48]


def _roster_from(payload: dict) -> list[str]:
    raw = payload.get("roster") or []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.replace(",", "\n").splitlines()]
    out = []
    seen = set()
    for name in raw:
        name = " ".join(str(name).split())
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _seeds_from(payload: dict) -> list[str]:
    raw = payload.get("seeds") or payload.get("core") or []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.replace(",", "\n").splitlines()]
    out = []
    seen = set()
    for name in raw:
        name = " ".join(str(name).split())
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out[:6]


def _best_roster_core(corpus, roster: list[str], max_megas: int) -> list[str]:
    """Search roster cores and keep the Bring-6 with the strongest format support."""
    owned = []
    seen = set()
    for name in roster:
        canon = corpus.resolve(name).canonical or name
        fam = recs._species_clause_key(canon)
        if fam in seen or corpus.support(canon) <= 0:
            continue
        seen.add(fam)
        owned.append(canon)
    owned.sort(key=lambda n: corpus.support(n), reverse=True)
    owned = owned[:16]
    if not owned:
        return []

    def grow(seed: str) -> tuple[float, list[str]]:
        names = [seed]
        families = {recs._species_clause_key(seed)}
        while len(names) < 6:
            pick = None
            for row in corpus.recommend_for_team(names, top_n=16, roster=owned):
                cand = row["pokemon"]
                fam = recs._species_clause_key(cand)
                if fam in families:
                    continue
                item = (corpus.top_item(cand) or (None, 0))[0]
                if max_megas and recs._count_megas(corpus, names) >= max_megas and recs._is_mega_slot(cand, item):
                    continue
                pick = cand
                break
            if pick is None:
                for cand in owned:
                    fam = recs._species_clause_key(cand)
                    if fam not in families:
                        pick = cand
                        break
            if pick is None:
                break
            names.append(pick)
            families.add(recs._species_clause_key(pick))
        return sum(corpus.support(n) for n in names), names

    best_score = -1.0
    best: list[str] = []
    for seed in owned[:8]:
        score, names = grow(seed)
        if score > best_score:
            best_score = score
            best = names
    return best


def _recommend(payload: dict) -> dict:
    with LOCK:
        if not STATE["ready"]:
            return {"ok": False, "error": STATE["error"] or "Corpus is still loading."}
        corpus = STATE["corpus"]
        pastes = STATE["pastes"]

    seeds = _seeds_from(payload)
    roster = _roster_from(payload)
    if not seeds and roster:
        if len(roster) < 2:
            return {"ok": False, "error": "Label at least two owned Pokémon first."}
        seeds = _best_roster_core(corpus, roster, int(payload.get("max_megas") or 2))
        if not seeds:
            return {"ok": False, "error": "None of the labeled names are in the current format data."}
    if not seeds:
        return {"ok": False, "error": "Pick at least one Pokémon."}

    max_megas = payload.get("max_megas")
    if max_megas is None:
        one_mega = payload.get("one_mega")
        if one_mega is None:
            max_megas = 2
        else:
            max_megas = 1 if bool(one_mega) else 0
    else:
        max_megas = int(max_megas)
        if max_megas < 0:
            max_megas = 0
    top_n = int(payload.get("top_n") or 8)

    resolved = [
        {
            "query": r.query,
            "canonical": r.canonical,
            "kind": r.kind,
            "reason": r.reason,
            "support": r.support,
            "item": r.item,
            "label": str(r),
        }
        for r in corpus.resolve_team(seeds)
    ]
    canonical = [r["canonical"] for r in resolved if r["canonical"]]

    partners = []
    roster = _roster_from(payload)
    for row in corpus.recommend_for_team(canonical, top_n=top_n, roster=roster):
        partners.append(
            {
                "pokemon": row["pokemon"],
                "avg_lift": round(row["avg_lift"], 2),
                "avg_together": row["avg_p_together"],
                "avg_together_pct": _pct(row["avg_p_together"]),
                "anchor_coverage": row["anchor_coverage"],
                "anchor_coverage_pct": _pct(row["anchor_coverage"]),
                "pair_winrate": row["pair_winrate"],
                "pair_winrate_pct": _pct(row["pair_winrate"]),
                "confidence": row["confidence"],
                "voters": row.get("voters") or [],
            }
        )

    sets = []
    for mon, prof in corpus.recommend_sets_for_team(canonical).items():
        sets.append(
            {
                "species": mon,
                "confidence": prof.confidence,
                "n_teams": prof.n_teams,
                "fallback": prof.fallback,
                "winrate": prof.winrate,
                "winrate_pct": _pct(prof.winrate),
                "items": [{"name": n, "share": p} for n, p in prof.top_items(4)],
                "moves": [{"name": n, "share": p} for n, p in prof.top_moves(4)],
                "abilities": [{"name": n, "share": p} for n, p in recs._top(recs._share(prof.abilities), 3)],
                "natures": [{"name": n, "share": p} for n, p in recs._top(recs._share(prof.natures), 3)],
            }
        )

    raw_moves = payload.get("seed_moves") or payload.get("moves") or []
    locked = {}
    rows = []
    if isinstance(raw_moves, dict):
        rows = [{"pokemon": k, "move": v} for k, v in raw_moves.items()]
    elif isinstance(raw_moves, list):
        for i, row in enumerate(raw_moves[:2]):
            if isinstance(row, dict):
                rows.append(row)
            else:
                poke = seeds[i] if i < len(seeds) else ""
                rows.append({"pokemon": poke, "move": row})
    for row in rows:
        poke = " ".join(str(row.get("pokemon") or "").split())
        move = " ".join(str(row.get("move") or "").split())
        if not poke or not move:
            continue
        resolved_one = corpus.resolve(poke)
        if resolved_one.canonical:
            locked[recs._norm(resolved_one.canonical)] = [move]
    built = _build_team(corpus, canonical, pastes, max_megas, locked_moves=locked, roster=roster)
    slots_out = []
    for slot in built.slots:
        _prefer_unburden(slot)
        types = pokemon_types(slot.species)
        slots_out.append(
            {
                "species": slot.species,
                "item": slot.item,
                "ability": slot.ability,
                "nature": slot.nature,
                "stat_points": recs.format_stat_points(slot.stat_points),
                "moves": slot.moves,
                "confidence": slot.confidence,
                "notes": slot.notes,
                "paste": slot.as_paste(),
                "types": types,
                "type_colors": type_colors(types),
            }
        )
    team = {
        "max_megas": max_megas,
        "paste": built.as_paste(),
        "slots": slots_out,
    }
    threats = []
    try:
        for row in recs.threats_for_team(
            corpus,
            [s.species for s in built.slots] or canonical,
            top_n=6,
        ):
            threats.append(
                {
                    "pokemon": row["pokemon"],
                    "reason": row["reason"],
                    "usage_pct": round(row.get("usage_pct") or 0.0, 1),
                    "games": int(row.get("games") or 0),
                    "vs": row.get("vs") or [],
                }
            )
    except Exception:
        threats = []
    return {
        "ok": True,
        "resolved": resolved,
        "partners": partners,
        "sets": sets,
        "team": team,
        "why": _explain(resolved, partners, sets, team, max_megas),
        "threats": threats,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            with LOCK:
                self._json(
                    200,
                    {
                        "ready": STATE["ready"],
                        "error": STATE["error"],
                        "teams": STATE["teams"],
                        "weight": STATE["weight"],
                        "pokemon": len(STATE["names"]),
                        "format": recs.FORMAT_RANKED,
                        "format_label": "Regulation M-C",
                        "updated": STATE.get("updated"),
                    },
                )
            return
        if path == "/api/pokemon":
            with LOCK:
                self._json(200, {"ready": STATE["ready"], "names": STATE["names"]})
            return
        if path == "/api/moves":
            query = parse_qs(urlparse(self.path).query)
            name = " ".join((query.get("name") or [""])[0].split())
            if not name:
                self._json(200, {"name": "", "moves": []})
                return
            with LOCK:
                corpus = STATE["corpus"]
                pastes = STATE["pastes"]
            if not STATE["ready"] or corpus is None:
                self._json(200, {"name": name, "moves": []})
                return
            resolved = corpus.resolve(name)
            canon = resolved.canonical or name
            bag: dict[str, float] = {}
            for src in (corpus, pastes):
                if src is None:
                    continue
                for mv, share in src.profile(canon, fallback=True).top_moves(16):
                    bag[mv] = max(bag.get(mv, 0.0), share)
            ordered = sorted(bag.items(), key=lambda kv: (-kv[1], kv[0]))
            self._json(
                200,
                {
                    "name": canon,
                    "moves": [{"name": n, "share": p} for n, p in ordered],
                },
            )
            return
        if path in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in ("/api/recommend", "/api/roster"):
            self._json(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "invalid JSON"})
            return
        payload = payload if isinstance(payload, dict) else {}
        if path == "/api/roster":
            try:
                with LOCK:
                    names = list(STATE["names"])
                matched, text, icons = _scan_images(payload.get("images") or [], names)
                typed = _match_roster_text(str(payload.get("text") or ""), names)
                merged = []
                seen = set()
                for name in matched + typed:
                    if name.casefold() in seen:
                        continue
                    seen.add(name.casefold())
                    merged.append(name)
                self._json(200, {"ok": True, "roster": merged, "ocr": text[:1500], "count": len(merged), "icons": icons})
            except ModuleNotFoundError as exc:
                missing = getattr(exc, "name", str(exc))
                self._json(200, {
                    "ok": False,
                    "error": f"Missing {missing}. In the server folder run: py -3 -m pip install pillow",
                })
            except Exception as exc:
                traceback.print_exc()
                self._json(200, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return
        try:
            self._json(200, _recommend(payload))
        except Exception as exc:
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def main() -> None:
    threading.Thread(target=_refresh_loop, daemon=True).start()
    host, port = "127.0.0.1", 8765
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Champions VGC teambuilder  →  http://{host}:{port}")
    print("Loading Limitless cups + 2026 Worlds lists in the background…")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
