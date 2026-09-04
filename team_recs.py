#!/usr/bin/env python3
"""Champions VGC teammate + team-specific set recommendations.

Built for Pokemon Champions VGC (Doubles), not Smogon singles.

Name resolution
  "Chesnaught" is not left as the 12-team berry set if "Chesnaught-Mega"
  (@ Chesnaughtite) is what the format actually uses. Base names resolve to
  the forme the corpus plays: highest-usage mega in the family, then the
  mega-stone holder, then the raw name.

Off-meta cores
  A team like Mega Chesnaught + Basculegion + Sinistcha is allowed.
  Partners are scored against the members that actually have sample size.
  Sets fall back to each mon's global set when the exact slice is empty.
"""

from __future__ import annotations

import json
import math
import re
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Iterator


FORMAT_RANKED = "battledataregmbs3"
FORMAT_TOURS = "championstournaments"
DEFAULT_MONTH = "2026-05"
DEFAULT_CUTOFF = 1760
PIKA = "https://www.pikalytics.com"
OFFICIAL = "https://championsbattledata.com"
POKEDATA = "https://www.pokedata.ovh"
WORLDS_2026_ID = "0000191"
UA = "ChampionsVGCTeamRecs/0.4 (+personal teambuilding tool)"

# Members below this many weighted teams are "off-meta" for voting.
SUPPORT_FLOOR = 25.0
# Dampen lift from 1-team coincidences: n / (n + this).
LIFT_PRIOR = 8.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(name: str) -> str:
    return " ".join(name.strip().split()).casefold()


def _top(counter: dict[str, float], n: int) -> list[tuple[str, float]]:
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def _share(counter: dict[str, float]) -> dict[str, float]:
    total = sum(counter.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in counter.items()}


def _http_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _pct(value) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).strip().rstrip("%"))


def _move_names(moves) -> list[str]:
    out = []
    for mv in moves or []:
        if isinstance(mv, str):
            out.append(mv)
        elif isinstance(mv, dict) and mv.get("name"):
            out.append(str(mv["name"]))
    return out


def _parse_record(record: str | None) -> tuple[int, int]:
    if not record:
        return (0, 0)
    parts = [p for p in record.replace("–", "-").split("-") if p.strip()]
    try:
        wins = int(parts[0])
        losses = int(parts[1]) if len(parts) > 1 else 0
        return wins, losses
    except ValueError:
        return (0, 0)


def _is_mega_name(name: str) -> bool:
    return bool(re.search(r"-mega(?:-[xy])?$", _norm(name)))


def _is_mega_stone(item: str | None) -> bool:
    if not item:
        return False
    k = _norm(item)
    if "white herb" in k or "expert" in k:
        return False
    return bool(re.search(r"(ite|nite)(?:\s+[xy])?$", k)) and "leftovers" not in k


def _family_key(name: str) -> str:
    """charizard-mega-y / mega charizard y / charizard -> charizard."""
    k = _norm(name)
    k = k.replace("'", "").replace(".", "")
    k = re.sub(r"^mega\s+", "", k)
    k = re.sub(r"\s+mega(?:\s+[xy])?$", "", k)
    k = re.sub(r"-mega(?:-[xy])?$", "", k)
    k = k.replace(" ", "")
    return k


# Forme tails that still share a species clause with the base name.
_FORME_TAIL = re.compile(
    r"(?:"
    r"-mega(?:-[xy])?"
    r"|-alola|-galar|-hisui"
    r"|-paldea-aqua|-paldea-blaze|-paldea-combat|-paldea"
    r"|-therian|-incarnate"
    r"|-rapid-strike|-single-strike|-rapidstrike|-singlestrike"
    r"|-wash|-heat|-fan|-mow|-frost"
    r"|-attack|-defense|-speed|-origin|-altered"
    r"|-bloodmoon|-wellspring|-hearthflame|-cornerstone|-teal"
    r"|-dusk-mane|-dawn-wings|-duskmane|-dawnwings"
    r"|-crowned|-hero|-zero|-school"
    r"|-female|-male|-(?:f|m)"
    r")$",
    re.I,
)


def _species_clause_key(name: str) -> str:
    """Venusaur / Venusaur-Mega / Mega Venusaur → one illegal-together key."""
    k = _family_key(name)
    prev = None
    while k and k != prev:
        prev = k
        k = _FORME_TAIL.sub("", k)
    return k


def _looks_like_query_wants_mega(name: str) -> bool:
    k = _norm(name)
    return k.startswith("mega ") or " mega" in k or "-mega" in k


# ---------------------------------------------------------------------------
# Team paste layer
# ---------------------------------------------------------------------------

@dataclass
class PokemonSet:
    species: str
    moves: list[str] = field(default_factory=list)
    item: str | None = None
    ability: str | None = None
    tera: str | None = None
    nature: str | None = None
    evs: str | None = None

    @property
    def key(self) -> str:
        return _norm(self.species)


@dataclass
class Team:
    members: list[PokemonSet]
    team_id: str = ""
    source: str = ""
    weight: float = 1.0
    wins: float = 0.0
    losses: float = 0.0

    def species(self) -> set[str]:
        return {m.key for m in self.members}

    def get(self, species: str) -> PokemonSet | None:
        target = _norm(species)
        for m in self.members:
            if m.key == target:
                return m
        return None

    def contains_all(self, names: Iterable[str]) -> bool:
        have = self.species()
        return all(_norm(n) in have for n in names)


@dataclass
class ResolvedName:
    query: str
    canonical: str
    kind: str
    reason: str
    support: float = 0.0
    item: str | None = None

    def __str__(self) -> str:
        if _norm(self.query) == _norm(self.canonical):
            return self.canonical
        return f"{self.query} → {self.canonical}"


@dataclass
class SlotProfile:
    species: str
    teammates: tuple[str, ...]
    n_teams: float
    moves: dict[str, float]
    items: dict[str, float]
    abilities: dict[str, float]
    teras: dict[str, float]
    natures: dict[str, float]
    wins: float = 0.0
    losses: float = 0.0
    fallback: str | None = None

    def top_moves(self, n: int = 8) -> list[tuple[str, float]]:
        return _top(_share(self.moves), n)

    def top_items(self, n: int = 5) -> list[tuple[str, float]]:
        return _top(_share(self.items), n)

    @property
    def winrate(self) -> float | None:
        games = self.wins + self.losses
        return self.wins / games if games else None

    @property
    def confidence(self) -> str:
        if self.n_teams >= 200:
            return "high"
        if self.n_teams >= SUPPORT_FLOOR:
            return "medium"
        if self.n_teams > 0:
            return "low"
        return "none"


@dataclass
class SetDelta:
    species: str
    teammates: tuple[str, ...]
    n_global: float
    n_conditional: float
    move_up: list[tuple[str, float, float]]
    move_down: list[tuple[str, float, float]]
    item_up: list[tuple[str, float, float]]
    item_down: list[tuple[str, float, float]]


class TeamCorpus:
    """Bag of Champions VGC teams. This is what makes pair-specific sets possible."""

    def __init__(self, teams: Iterable[Team] | None = None):
        self.teams: list[Team] = list(teams or [])
        self._index_ready = False
        self._by_key: dict[str, str] = {}
        self._weight: dict[str, float] = {}
        self._items: dict[str, dict[str, float]] = {}
        self._family: dict[str, list[str]] = {}

    def add(self, team: Team) -> None:
        self.teams.append(team)
        self._index_ready = False

    def extend(self, teams: Iterable[Team]) -> None:
        self.teams.extend(teams)
        self._index_ready = False

    def __len__(self) -> int:
        return len(self.teams)

    @property
    def total_weight(self) -> float:
        return sum(t.weight for t in self.teams)

    def _ensure_index(self) -> None:
        if self._index_ready:
            return
        by_key: dict[str, str] = {}
        weight: dict[str, float] = defaultdict(float)
        items: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        family: dict[str, set[str]] = defaultdict(set)
        for team in self.teams:
            seen = set()
            for m in team.members:
                if m.key in seen:
                    continue
                seen.add(m.key)
                by_key.setdefault(m.key, m.species)
                weight[m.key] += team.weight
                family[_family_key(m.species)].add(m.key)
                if m.item:
                    items[m.key][m.item] += team.weight
        self._by_key = by_key
        self._weight = dict(weight)
        self._items = {k: dict(v) for k, v in items.items()}
        self._family = {k: sorted(v) for k, v in family.items()}
        self._index_ready = True

    def display(self, species: str) -> str:
        self._ensure_index()
        return self._by_key.get(_norm(species), species)

    def support(self, species: str) -> float:
        self._ensure_index()
        return float(self._weight.get(_norm(species), 0.0))

    def usage(self, species: str) -> float:
        w = self.support(species)
        total = self.total_weight
        return w / total if total else 0.0

    def top_item(self, species: str) -> tuple[str, float] | None:
        self._ensure_index()
        bag = self._items.get(_norm(species)) or {}
        if not bag:
            return None
        item, w = max(bag.items(), key=lambda kv: kv[1])
        return item, w

    def resolve(self, name: str) -> ResolvedName:
        """Map a casual name onto the forme the format actually uses."""
        self._ensure_index()
        raw = " ".join(name.strip().split())
        if not raw:
            return ResolvedName(name, name, "empty", "blank name")

        key = _norm(raw)
        if key in self._by_key:
            # Exact hit. Still promote to mega when the typed name is the
            # base and a mega in the same family is clearly the real set.
            exact = self._by_key[key]
            if _is_mega_name(exact) or _looks_like_query_wants_mega(raw):
                item = (self.top_item(exact) or (None, 0))[0]
                return ResolvedName(raw, exact, "exact", "name matches corpus", self.support(exact), item)
            promoted = self._promote_to_played_forme(raw, exact)
            if promoted is not None:
                return promoted
            item = (self.top_item(exact) or (None, 0))[0]
            return ResolvedName(raw, exact, "exact", "name matches corpus", self.support(exact), item)

        # "mega chesnaught" / "chesnaught mega y"
        wanted_mega = _looks_like_query_wants_mega(raw)
        fam = _family_key(raw)
        candidates = [self._by_key[k] for k in self._family.get(fam, []) if k in self._by_key]
        if candidates:
            picked = self._pick_played_forme(candidates, prefer_mega=wanted_mega or True)
            item = (self.top_item(picked) or (None, 0))[0]
            kind = "mega-from-usage" if _is_mega_name(picked) else "forme-from-usage"
            reason = f"family {fam} → highest-usage played forme"
            if item and _is_mega_stone(item):
                kind = "mega-from-item"
                reason = f"family {fam} holds {item}"
            return ResolvedName(raw, picked, kind, reason, self.support(picked), item)

        # Last chance: substring / starts-with among indexed names.
        hits = [disp for k, disp in self._by_key.items() if fam and fam in k.replace(" ", "").replace("-", "")]
        if hits:
            picked = self._pick_played_forme(hits, prefer_mega=True)
            item = (self.top_item(picked) or (None, 0))[0]
            return ResolvedName(raw, picked, "fuzzy", "closest corpus name", self.support(picked), item)

        return ResolvedName(raw, raw, "unknown", "not in corpus", 0.0, None)

    def _promote_to_played_forme(self, query: str, exact: str) -> ResolvedName | None:
        fam = _family_key(exact)
        relatives = [self._by_key[k] for k in self._family.get(fam, []) if k in self._by_key]
        if len(relatives) <= 1:
            return None
        picked = self._pick_played_forme(relatives, prefer_mega=True)
        if _norm(picked) == _norm(exact):
            return None
        exact_w = self.support(exact)
        picked_w = self.support(picked)
        # Only auto-assume mega/forme when the played version is clearly ahead.
        if picked_w < max(SUPPORT_FLOOR, exact_w * 1.5):
            return None
        item = (self.top_item(picked) or (None, 0))[0]
        if _is_mega_name(picked) or (item and _is_mega_stone(item)):
            kind = "mega-from-item" if item and _is_mega_stone(item) else "mega-from-usage"
            reason = (
                f"{exact} is {exact_w:.0f} teams; {picked} is {picked_w:.0f}"
                + (f" holding {item}" if item else "")
            )
            return ResolvedName(query, picked, kind, reason, picked_w, item)
        return None

    def _pick_played_forme(self, names: list[str], *, prefer_mega: bool) -> str:
        def score(n: str) -> tuple:
            w = self.support(n)
            item = (self.top_item(n) or (None, 0.0))
            stone = 1 if item and _is_mega_stone(item[0]) else 0
            mega = 1 if _is_mega_name(n) else 0
            return (w, stone if prefer_mega else 0, mega if prefer_mega else 0)

        return max(names, key=score)

    def resolve_team(self, names: Iterable[str]) -> list[ResolvedName]:
        return [self.resolve(n) for n in names]

    def _matching(self, species: str, teammates: Iterable[str] = ()) -> Iterator[tuple[Team, PokemonSet]]:
        resolved = self.resolve(species)
        required = [resolved.canonical, *[self.resolve(t).canonical for t in teammates]]
        for team in self.teams:
            if not team.contains_all(required):
                continue
            slot = team.get(resolved.canonical)
            if slot is not None:
                yield team, slot

    def profile(
        self,
        species: str,
        teammates: Iterable[str] = (),
        *,
        fallback: bool = True,
    ) -> SlotProfile:
        resolved = self.resolve(species)
        mates = tuple(self.resolve(t).canonical for t in teammates)
        moves: dict[str, float] = defaultdict(float)
        items: dict[str, float] = defaultdict(float)
        abilities: dict[str, float] = defaultdict(float)
        teras: dict[str, float] = defaultdict(float)
        natures: dict[str, float] = defaultdict(float)
        n = wins = losses = 0.0
        for team, slot in self._matching(resolved.canonical, mates):
            w = team.weight
            n += w
            wins += team.wins
            losses += team.losses
            for mv in slot.moves:
                moves[mv] += w
            if slot.item:
                items[slot.item] += w
            if slot.ability:
                abilities[slot.ability] += w
            if slot.tera:
                teras[slot.tera] += w
            if slot.nature:
                natures[slot.nature] += w

        used_fallback = None
        if fallback and mates and n < SUPPORT_FLOOR:
            why = "global" if n <= 0 else f"global (only {n:.0f} pair teams)"
            return self.profile(resolved.canonical, teammates=(), fallback=False)._copy_as_fallback(
                resolved.canonical, mates, why
            )

        return SlotProfile(
            species=resolved.canonical,
            teammates=mates,
            n_teams=n,
            moves=dict(moves),
            items=dict(items),
            abilities=dict(abilities),
            teras=dict(teras),
            natures=dict(natures),
            wins=wins,
            losses=losses,
            fallback=used_fallback,
        )

    def deltas(
        self,
        species: str,
        teammates: Iterable[str],
        *,
        min_delta: float = 0.06,
        top_n: int = 6,
    ) -> SetDelta:
        global_p = self.profile(species, fallback=False)
        cond_p = self.profile(species, teammates, fallback=False)

        def changes(global_c: dict[str, float], cond_c: dict[str, float]):
            g, c = _share(global_c), _share(cond_c)
            rows = []
            for nm in set(g) | set(c):
                cv, gv = c.get(nm, 0.0), g.get(nm, 0.0)
                rows.append((nm, cv, cv - gv))
            rows.sort(key=lambda t: -t[2])
            up = [t for t in rows if t[2] >= min_delta][:top_n]
            down = [t for t in reversed(rows) if t[2] <= -min_delta][:top_n]
            return up, down

        move_up, move_down = changes(global_p.moves, cond_p.moves)
        item_up, item_down = changes(global_p.items, cond_p.items)
        return SetDelta(
            species=self.resolve(species).canonical,
            teammates=tuple(self.resolve(t).canonical for t in teammates),
            n_global=global_p.n_teams,
            n_conditional=cond_p.n_teams,
            move_up=move_up,
            move_down=move_down,
            item_up=item_up,
            item_down=item_down,
        )

    def recommend_teammates(
        self,
        species: str,
        *,
        exclude: Iterable[str] = (),
        top_n: int = 10,
        min_together: float = 0.04,
    ) -> list[dict]:
        resolved = self.resolve(species)
        blocked = {
            _norm(resolved.canonical),
            *(_norm(self.resolve(x).canonical) for x in exclude),
        }
        with_species = [t for t in self.teams if _norm(resolved.canonical) in t.species()]
        total = sum(t.weight for t in with_species)
        if total <= 0:
            return []

        pair_w: dict[str, float] = defaultdict(float)
        pair_wins: dict[str, float] = defaultdict(float)
        pair_losses: dict[str, float] = defaultdict(float)
        self._ensure_index()
        all_w = self.total_weight or 1.0

        for team in with_species:
            seen = set()
            for m in team.members:
                if m.key in blocked or m.key in seen:
                    continue
                seen.add(m.key)
                pair_w[m.key] += team.weight
                pair_wins[m.key] += team.wins
                pair_losses[m.key] += team.losses

        rows = []
        for key, w in pair_w.items():
            p_together = w / total
            if p_together < min_together:
                continue
            usage = self._weight.get(key, 0.0) / all_w
            raw_lift = p_together / usage if usage else 0.0
            shrink = w / (w + LIFT_PRIOR)
            lift = raw_lift * shrink
            games = pair_wins[key] + pair_losses[key]
            wr = pair_wins[key] / games if games else None
            wr_term = wr if wr is not None else 0.5
            confidence = "high" if w >= 200 else "medium" if w >= SUPPORT_FLOOR else "low"
            rows.append(
                {
                    "pokemon": self._by_key.get(key, key),
                    "p_together": p_together,
                    "partner_usage": usage,
                    "lift": lift,
                    "raw_lift": raw_lift,
                    "pair_winrate": wr,
                    "n": w,
                    "confidence": confidence,
                    "score": lift * math.log1p(w) * wr_term,
                }
            )
        rows.sort(key=lambda r: (-r["score"], -r["p_together"], r["pokemon"]))
        return rows[:top_n]

    def recommend_for_team(
        self,
        team: Iterable[str],
        *,
        top_n: int = 10,
        min_together: float = 0.03,
    ) -> list[dict]:
        """Next pick given a partial team.

        Off-meta members still count, but partners must fit the members
        that have real sample size. A 50-team Mega Chesnaught does not
        outvote a 3,400-team Basculegion.
        """
        resolved = self.resolve_team(team)
        names = [r.canonical for r in resolved]
        supports = [max(self.support(n), 0.0) for n in names]
        peak = max(supports) if supports else 0.0
        # An "anchor" is a member the format actually plays. A 50-team mega
        # does not get to overrule a 3,000-team partner on the same core.
        relative_floor = max(SUPPORT_FLOOR, 0.15 * peak)
        anchors = [n for n, s in zip(names, supports) if s >= relative_floor]
        if not anchors:
            best = max(range(len(names)), key=lambda i: supports[i]) if names else None
            anchors = [names[best]] if best is not None and supports[best] > 0 else names

        blocked = {_norm(n) for n in names}
        blocked_families = {_species_clause_key(n) for n in names}
        scores: dict[str, dict] = {}
        voter_weight = {n: math.log1p(self.support(n)) for n in names}

        for member in names:
            recs = self.recommend_teammates(
                member, exclude=blocked, top_n=40, min_together=min_together
            )
            vw = voter_weight[member] or 0.0
            is_anchor = member in anchors
            for rec in recs:
                poke = rec["pokemon"]
                if _norm(poke) in blocked or _species_clause_key(poke) in blocked_families:
                    continue
                bucket = scores.setdefault(
                    poke,
                    {
                        "pokemon": poke,
                        "lifts": [],
                        "p_togethers": [],
                        "winrates": [],
                        "ns": [],
                        "voters": [],
                        "anchor_hits": 0,
                        "weighted_lift": 0.0,
                        "weight_sum": 0.0,
                    },
                )
                bucket["lifts"].append(rec["lift"])
                bucket["p_togethers"].append(rec["p_together"])
                if rec["pair_winrate"] is not None:
                    bucket["winrates"].append(rec["pair_winrate"])
                bucket["ns"].append(rec["n"])
                bucket["voters"].append(member)
                if is_anchor:
                    bucket["anchor_hits"] += 1
                bucket["weighted_lift"] += rec["lift"] * vw
                bucket["weight_sum"] += vw

        n_anchors = max(len(anchors), 1)
        rows = []
        for poke, bucket in scores.items():
            anchor_cover = bucket["anchor_hits"] / n_anchors
            if anchor_cover <= 0 and anchors:
                continue
            wsum = bucket["weight_sum"] or 1.0
            avg_lift = bucket["weighted_lift"] / wsum
            avg_p = sum(bucket["p_togethers"]) / len(bucket["p_togethers"])
            wr = (
                sum(bucket["winrates"]) / len(bucket["winrates"])
                if bucket["winrates"]
                else None
            )
            wr_term = wr if wr is not None else 0.5
            n_pair = sum(bucket["ns"])
            confidence = "high" if n_pair >= 200 and anchor_cover == 1 else (
                "medium" if n_pair >= SUPPORT_FLOOR else "low"
            )
            rows.append(
                {
                    "pokemon": poke,
                    "avg_lift": avg_lift,
                    "min_lift": min(bucket["lifts"]),
                    "avg_p_together": avg_p,
                    "coverage": len(bucket["lifts"]) / max(len(names), 1),
                    "anchor_coverage": anchor_cover,
                    "pair_winrate": wr,
                    "confidence": confidence,
                    "voters": bucket["voters"],
                    "score": avg_lift * anchor_cover * wr_term * math.log1p(n_pair),
                }
            )
        rows.sort(key=lambda r: (-r["score"], -r["anchor_coverage"], r["pokemon"]))
        return rows[:top_n]

    def recommend_sets_for_team(self, team_species: Iterable[str]) -> dict[str, SlotProfile]:
        resolved = self.resolve_team(team_species)
        names = [r.canonical for r in resolved]
        out: dict[str, SlotProfile] = {}
        for species in names:
            others = [n for n in names if _norm(n) != _norm(species)]
            out[species] = self.profile(species, others, fallback=True)
        return out


def _copy_as_fallback(self: SlotProfile, species: str, teammates: tuple[str, ...], why: str) -> SlotProfile:
    return SlotProfile(
        species=species,
        teammates=teammates,
        n_teams=self.n_teams,
        moves=self.moves,
        items=self.items,
        abilities=self.abilities,
        teras=self.teras,
        natures=self.natures,
        wins=self.wins,
        losses=self.losses,
        fallback=why,
    )


SlotProfile._copy_as_fallback = _copy_as_fallback  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Pikalytics + official ranked loaders
# ---------------------------------------------------------------------------

def load_team_usage(fmt: str = FORMAT_TOURS) -> TeamCorpus:
    payload = _http_json(f"{PIKA}/api/team-usage/{fmt}")
    corpus = TeamCorpus()
    for group in payload.get("groups") or []:
        pokemon = group.get("pokemon") or []
        if len(pokemon) < 2:
            continue
        uses = float(group.get("uses") or 0) or 1.0
        wins = float(group.get("wins") or 0)
        losses = float(group.get("losses") or 0)
        members = []
        for slot in pokemon:
            name = slot.get("name") or slot.get("pokemon")
            if not name:
                continue
            members.append(PokemonSet(str(name), item=slot.get("item")))
        if len(members) < 2:
            continue
        corpus.add(
            Team(
                members=members,
                team_id=str(group.get("key") or ""),
                source=f"pikalytics-team-usage:{fmt}",
                weight=uses,
                wins=wins,
                losses=losses,
            )
        )
    return corpus


def _team_from_pika_paste(raw: dict, source: str) -> Team | None:
    slots = raw.get("pokemon") or []
    members = []
    for slot in slots:
        name = slot.get("name") or slot.get("display_name")
        if not name:
            continue
        members.append(
            PokemonSet(
                species=str(name),
                moves=_move_names(slot.get("moves")),
                item=slot.get("item"),
                ability=slot.get("ability"),
            )
        )
    if len(members) < 2:
        return None
    wins, losses = _parse_record(raw.get("record"))
    weight = 1.0 + math.log1p(wins)
    return Team(
        members=members,
        team_id=str(raw.get("link") or raw.get("author") or ""),
        source=source,
        weight=weight,
        wins=float(wins),
        losses=float(losses),
    )


def load_sample_pastes(fmt: str = FORMAT_TOURS) -> TeamCorpus:
    payload = _http_json(f"{PIKA}/api/topteams/{fmt}")
    corpus = TeamCorpus()
    seen = set()
    for raw in payload.get("landingTeams") or []:
        team = _team_from_pika_paste(raw, f"pikalytics-topteams:{fmt}")
        if team is None:
            continue
        sig = tuple(sorted(m.key for m in team.members))
        if sig in seen:
            continue
        seen.add(sig)
        corpus.add(team)
    return corpus


def fetch_ranked_list(
    month: str = DEFAULT_MONTH,
    fmt: str = FORMAT_RANKED,
    cutoff: int = DEFAULT_CUTOFF,
) -> list[dict]:
    return list(_http_json(f"{PIKA}/api/l/{month}/{fmt}-{cutoff}"))


def fetch_mon_detail(
    name: str,
    month: str = DEFAULT_MONTH,
    fmt: str = FORMAT_RANKED,
    cutoff: int = DEFAULT_CUTOFF,
) -> dict:
    slug = name.strip().replace(" ", "").replace(".", "").replace("'", "")
    return dict(_http_json(f"{PIKA}/api/p/{month}/{fmt}-{cutoff}/{slug}"))


def pastes_from_mon_detail(detail: dict) -> list[Team]:
    teams = []
    for raw in detail.get("teams") or []:
        team = _team_from_pika_paste(raw, f"pikalytics-mon:{detail.get('name')}")
        if team is not None:
            teams.append(team)
    return teams


STAT_ORDER = ("hp", "atk", "def", "spa", "spd", "spe")
STAT_LABELS = {
    "hp": "HP",
    "atk": "Atk",
    "def": "Def",
    "spa": "SpA",
    "spd": "SpD",
    "spe": "Spe",
}


def parse_stat_points(text: str | None) -> dict[str, int]:
    """Pikalytics stores SP as '2/32/0/0/0/32' (HP/Atk/Def/SpA/SpD/Spe)."""
    if not text:
        return {}
    parts = []
    for chunk in str(text).replace("-", "/").split("/"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            parts.append(int(float(chunk)))
        except ValueError:
            return {}
    if len(parts) != 6:
        return {}
    return dict(zip(STAT_ORDER, parts))


def format_stat_points(spread: dict[str, int] | None) -> str:
    if not spread:
        return ""
    bits = [f"{spread[k]} {STAT_LABELS[k]}" for k in STAT_ORDER if spread.get(k)]
    return " / ".join(bits)


def _is_mega_slot(species: str, item: str | None = None) -> bool:
    return _is_mega_name(species) or _is_mega_stone(item)


@dataclass
class BuiltSlot:
    species: str
    item: str | None = None
    ability: str | None = None
    nature: str | None = None  # stat alignment
    stat_points: dict[str, int] = field(default_factory=dict)
    moves: list[str] = field(default_factory=list)
    confidence: str = "none"
    notes: list[str] = field(default_factory=list)

    def as_paste(self) -> str:
        lines = [f"{self.species}" + (f" @ {self.item}" if self.item else "")]
        if self.ability:
            lines.append(f"Ability: {self.ability}")
        if self.nature:
            lines.append(f"Stat Alignment: {self.nature}")
        sp = format_stat_points(self.stat_points)
        if sp:
            lines.append(f"Stat Points: {sp}")
        for mv in self.moves[:4]:
            lines.append(f"- {mv}")
        return "\n".join(lines)


@dataclass
class BuiltTeam:
    slots: list[BuiltSlot]
    resolved: list[ResolvedName] = field(default_factory=list)

    def species(self) -> list[str]:
        return [s.species for s in self.slots]

    def as_paste(self) -> str:
        return "\n\n".join(slot.as_paste() for slot in self.slots)


def _pick_named(rows: list[dict], key: str) -> str | None:
    if not rows:
        return None
    row = rows[0]
    return row.get(key) or row.get("name")


def _best_pair_profile(corpus: TeamCorpus, name: str, teammates: list[str]) -> SlotProfile:
    """Use the strongest 2-mon slice, not the whole 6-mon team (too sparse)."""
    best: SlotProfile | None = None
    for mate in teammates:
        prof = corpus.profile(name, [mate], fallback=False)
        if prof.n_teams < SUPPORT_FLOOR:
            continue
        if best is None or prof.n_teams > best.n_teams:
            best = prof
    return best if best is not None else corpus.profile(name, fallback=False)


def _merge_detail(*blobs: dict | None) -> dict:
    merged: dict = {}
    for blob in blobs:
        if not blob:
            continue
        for key in ("abilities", "natures", "moves", "spreads", "items", "name", "name_trans"):
            if key not in merged or not merged.get(key):
                if blob.get(key):
                    merged[key] = blob[key]
    return merged


def fill_slot(
    corpus: TeamCorpus,
    species: str,
    teammates: Iterable[str] = (),
    *,
    detail: dict | None = None,
    pastes: TeamCorpus | None = None,
) -> BuiltSlot:
    """Fill one Champions slot from compositions + ranked detail + pastes."""
    resolved = corpus.resolve(species)
    name = resolved.canonical
    mates = [corpus.resolve(t).canonical for t in teammates if _norm(t) != _norm(name)]
    notes: list[str] = []
    if resolved.kind not in ("exact", "empty") and _norm(resolved.query) != _norm(name):
        notes.append(str(resolved))

    item_prof = _best_pair_profile(corpus, name, mates)
    if item_prof.teammates:
        notes.append(f"items vs {item_prof.teammates[0]} n={item_prof.n_teams:.0f}")
    else:
        notes.append("items global")
    top_items = item_prof.top_items(1)
    item = top_items[0][0] if top_items else resolved.item

    if resolved.item and _is_mega_stone(resolved.item):
        item = resolved.item

    moves: list[str] = []
    if pastes is not None:
        move_prof = pastes.profile(name, fallback=True)
        pair_moves = None
        if mates:
            pair_moves = pastes.profile(name, mates[:1], fallback=False)
        use = pair_moves if pair_moves and pair_moves.n_teams >= 3 else move_prof
        moves = [mv for mv, _ in use.top_moves(4)]
        if moves:
            notes.append(f"moves from {use.n_teams:.0f} pastes")

    ability = nature = None
    spread: dict[str, int] = {}
    if detail:
        ability = _pick_named(detail.get("abilities") or [], "ability")
        nature = _pick_named(detail.get("natures") or [], "nature")
        spreads = detail.get("spreads") or []
        if spreads:
            spread = parse_stat_points(spreads[0].get("ev"))
        if not moves:
            ranked = detail.get("moves") or []
            moves = [m["move"] for m in ranked[:4] if m.get("move")]
            if moves:
                notes.append("moves from usage")

    confidence = item_prof.confidence
    if not moves:
        confidence = "low"

    return BuiltSlot(
        species=name,
        item=item,
        ability=ability,
        nature=nature,
        stat_points=spread,
        moves=moves,
        confidence=confidence,
        notes=notes,
    )


def _detail_slug(name: str) -> str:
    return name.strip().replace(" ", "").replace(".", "").replace("'", "")


def _safe_detail(name: str, fmt: str) -> dict | None:
    last = None
    for _ in range(2):
        try:
            return fetch_mon_detail(name, fmt=fmt)
        except Exception as exc:
            last = exc
    return None


def _detail_from_official(name: str) -> dict | None:
    try:
        live = official_doubles(name)
    except Exception:
        return None
    if not live.get("moves") and not live.get("items"):
        return None
    spreads = []
    # official stat_points rows live on the raw endpoint; skip if absent
    return {
        "name": live.get("pokemon") or name,
        "moves": [{"move": r["name"]} for r in live.get("moves") or [] if r.get("name")],
        "abilities": [{"ability": r["name"]} for r in live.get("abilities") or [] if r.get("name")],
        "natures": [{"nature": r["name"]} for r in live.get("natures") or [] if r.get("name")],
        "items": [{"item": r["name"]} for r in live.get("items") or [] if r.get("name")],
        "spreads": spreads,
    }


def load_details(names: Iterable[str]) -> dict[str, dict]:
    """Ranked page first; fill holes from tournament pages and the base forme."""
    out: dict[str, dict] = {}
    for name in names:
        ranked = _safe_detail(name, FORMAT_RANKED)
        tours = _safe_detail(name, FORMAT_TOURS)
        stripped = re.sub(r"-mega(?:-[xy])?$", "", name, flags=re.I)
        base = None
        if _is_mega_name(name) or (ranked and not (ranked.get("moves") and ranked.get("spreads"))):
            if stripped != name:
                base = _safe_detail(stripped, FORMAT_RANKED)
        merged = _merge_detail(ranked, tours, base)
        if not (merged.get("moves") and merged.get("spreads")):
            merged = _merge_detail(
                merged,
                _detail_from_official(name),
                _detail_from_official(stripped if stripped != name else name),
            )
        if not merged:
            continue
        out[_norm(name)] = merged
        disp = merged.get("name_trans") or merged.get("name")
        if disp:
            out[_norm(disp)] = merged
    return out


def _mega_cap(one_mega: bool | None, max_megas: int | None) -> int:
    """How many mega slots the auto-builder may keep. 0 = no cap."""
    if max_megas is not None:
        return max(0, int(max_megas))
    if one_mega is False:
        return 0
    return 1


def _count_megas(corpus: TeamCorpus, names: list[str]) -> int:
    n = 0
    for name in names:
        item = (corpus.top_item(name) or (None, 0))[0]
        if _is_mega_slot(name, item):
            n += 1
    return n


def _swap_off_megastone(corpus: TeamCorpus, slot: BuiltSlot, teammates: list[str]) -> None:
    """Keep the species, drop the stone for a common non-mega item."""
    if not _is_mega_stone(slot.item) and not _is_mega_name(slot.species):
        return
    prof = _best_pair_profile(corpus, slot.species, teammates)
    alt = None
    for name, _share in prof.top_items(8):
        if name and not _is_mega_stone(name):
            alt = name
            break
    slot.item = alt
    slot.notes.append("megastone dropped (cap)")


def _apply_mega_cap(
    corpus: TeamCorpus,
    slots: list[BuiltSlot],
    *,
    seed_count: int,
    max_megas: int,
) -> None:
    if max_megas <= 0:
        return
    mega_idxs = [i for i, s in enumerate(slots) if _is_mega_slot(s.species, s.item)]
    extra = len(mega_idxs) - max_megas
    if extra <= 0:
        return
    # Prefer stripping auto-filled partners, then later seeds.
    order = [i for i in reversed(mega_idxs) if i >= seed_count]
    order += [i for i in reversed(mega_idxs) if i < seed_count]
    teammates = [s.species for s in slots]
    for idx in order[:extra]:
        _swap_off_megastone(corpus, slots[idx], teammates)


def build_team(
    corpus: TeamCorpus,
    seeds: Iterable[str],
    *,
    size: int = 6,
    pastes: TeamCorpus | None = None,
    one_mega: bool | None = None,
    max_megas: int | None = None,
) -> BuiltTeam:
    """Grow a partial core into a full Champions Bring-6 team.

    `max_megas` caps how many mega-named / megastone partners are auto-added.
    0 means unlimited. Seeded megas are kept; extras on auto-picks get a
    non-stone item. `one_mega=True` is the old alias for `max_megas=1`.
    """
    cap = _mega_cap(one_mega, max_megas)
    resolved = corpus.resolve_team(seeds)
    raw_names = [r.canonical for r in resolved if r.canonical]
    names: list[str] = []
    seen = set()
    seen_families = set()
    for n in raw_names:
        fam = _species_clause_key(n)
        if _norm(n) in seen or fam in seen_families:
            continue
        names.append(n)
        seen.add(_norm(n))
        seen_families.add(fam)
    seed_count = len(names)

    while len(names) < size:
        recs = corpus.recommend_for_team(names, top_n=20)
        picked = None
        mega_count = _count_megas(corpus, names)
        for rec in recs:
            cand = rec["pokemon"]
            fam = _species_clause_key(cand)
            if _norm(cand) in seen or fam in seen_families:
                continue
            cand_item = (corpus.top_item(cand) or (None, 0))[0]
            if cap and mega_count >= cap and _is_mega_slot(cand, cand_item):
                continue
            picked = cand
            break
        if picked is None:
            break
        names.append(picked)
        seen.add(_norm(picked))
        seen_families.add(_species_clause_key(picked))

    details = load_details(names)
    slots = []
    for species in names:
        detail = details.get(_norm(species))
        slots.append(fill_slot(corpus, species, names, detail=detail, pastes=pastes))
    _apply_mega_cap(corpus, slots, seed_count=seed_count, max_megas=cap)
    return BuiltTeam(slots=slots, resolved=resolved)


def official_doubles(name: str, season: str = "Current") -> dict:
    slug = name.strip().lower().replace(" ", "").replace("-", "")
    url = f"{OFFICIAL}/api/battle/Doubles/{slug}?season={season}"
    payload = _http_json(url)
    rows = payload.get("rows") or []
    bucket: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        cat = row.get("category") or ""
        bucket[cat].append(row)
    return {
        "pokemon": payload.get("pokemon"),
        "source": payload.get("source"),
        "season": payload.get("season"),
        "moves": bucket.get("move", []),
        "items": bucket.get("held_item", []),
        "abilities": bucket.get("ability", []),
        "teammates": bucket.get("teammate", []),
        "natures": bucket.get("stat_alignment", []),
    }


# ---------------------------------------------------------------------------
# Official Play! Pokemon events (pokedata.ovh) + Limitless (Pikalytics)
# ---------------------------------------------------------------------------
#
# Pikalytics `championstournaments` is a rolling Limitless-cup snapshot.
# It does not include official Play! Pokemon events. Those live on
# pokedata.ovh (RK9 teamlists): Worlds, Internationals, Regionals, Specials.
#
# Worlds 2026 = pokedata id 0000191, 645 lists, open team sheets.

_FORME_SUFFIX = {
    "wash": "-Wash",
    "heat": "-Heat",
    "frost": "-Frost",
    "fan": "-Fan",
    "mow": "-Mow",
    "alola": "-Alola",
    "galar": "-Galar",
    "hisui": "-Hisui",
    "paldea": "-Paldea",
    "rapid strike": "-Rapid-Strike",
    "single strike": "-Single-Strike",
    "wellspring": "-Wellspring",
    "hearthflame": "-Hearthflame",
    "cornerstone": "-Cornerstone",
    "bloodmoon": "-Bloodmoon",
}


def _strip_pokedata_forme(name: str) -> tuple[str, str]:
    """'Floette [Eternal Flower]' -> ('Floette', 'eternal flower')."""
    raw = " ".join((name or "").strip().split())
    m = re.search(r"^(.*?)\s*\[([^\]]+)\]\s*$", raw)
    if not m:
        return raw, ""
    return m.group(1).strip(), m.group(2).strip().casefold()


def _pokedata_species(name: str, item: str | None = None) -> str:
    base, forme = _strip_pokedata_forme(name)
    if forme in ("male", "m"):
        species = base
    elif forme in ("female", "f"):
        species = f"{base}-F" if _norm(base) == "basculegion" else base
    elif forme in _FORME_SUFFIX:
        species = f"{base}{_FORME_SUFFIX[forme]}"
    elif forme.startswith("mega"):
        tail = forme.replace("mega", "").strip()
        species = f"{base}-Mega" + (f"-{tail.upper()}" if tail in ("x", "y") else "")
    else:
        species = base

    if item and _is_mega_stone(item) and not _is_mega_name(species):
        ik = _norm(item)
        if "charizardite y" in ik or ik.endswith(" y"):
            species = "Charizard-Mega-Y"
        elif "charizardite x" in ik or ik.endswith(" x"):
            species = "Charizard-Mega-X"
        else:
            species = f"{species}-Mega"
    return species


def _event_kind(name: str) -> str:
    k = _norm(name)
    if "world" in k:
        return "worlds"
    if "international" in k:
        return "international"
    if "special" in k:
        return "special"
    if "regional" in k:
        return "regional"
    return "other"


def _event_weight(kind: str, placing: int | None, wins: int, players: int = 0) -> float:
    base = {
        "worlds": 28.0,
        "international": 14.0,
        "special": 9.0,
        "regional": 7.0,
        "limitless": 1.6,
        "other": 4.0,
    }.get(kind, 4.0)
    place = 0.0
    if placing == 1:
        place = 18.0
    elif placing is not None and placing <= 4:
        place = 11.0
    elif placing is not None and placing <= 8:
        place = 7.0
    elif placing is not None and placing <= 16:
        place = 4.0
    elif placing is not None and placing <= 32:
        place = 2.0
    size = math.log1p(max(players, 0)) * (0.35 if kind == "limitless" else 0.15)
    return base + place + math.log1p(max(wins, 0)) + size


def list_official_events(year: int | None = 2026, *, min_decklists: int = 1) -> list[dict]:
    """Play! Pokemon VG events from pokedata.ovh (Worlds, ICs, Regionals)."""
    payload = _http_json(f"{POKEDATA}/apiv2/vg/tournaments")
    rows = ((payload.get("vg") or {}).get("data")) or payload.get("data") or []
    out = []
    for row in rows:
        start = str((row.get("date") or {}).get("start") or "")
        if year is not None and not start.startswith(str(year)):
            continue
        lists = int(row.get("decklists") or 0)
        if lists < min_decklists:
            continue
        name = row.get("name") or ""
        out.append(
            {
                "id": str(row.get("id") or ""),
                "name": name,
                "start": start,
                "end": str((row.get("date") or {}).get("end") or ""),
                "decklists": lists,
                "players": row.get("players") or {},
                "winners": row.get("winners") or {},
                "rk9": row.get("rk9link") or "",
                "kind": _event_kind(name),
                "status": row.get("tournamentStatus") or "",
            }
        )
    out.sort(key=lambda r: r["start"], reverse=True)
    return out


def _team_from_pokedata_player(
    player: dict,
    *,
    event_name: str,
    event_id: str,
    kind: str,
    players: int,
    division: str,
) -> Team | None:
    raw_list = player.get("decklist") or []
    members: list[PokemonSet] = []
    for slot in raw_list:
        raw_name = slot.get("name")
        if not raw_name:
            continue
        item = slot.get("item")
        species = _pokedata_species(str(raw_name), item)
        moves = slot.get("badges") or slot.get("moves") or []
        if moves and isinstance(moves[0], dict):
            moves = _move_names(moves)
        else:
            moves = [str(m) for m in moves if m]
        members.append(
            PokemonSet(
                species=species,
                moves=moves,
                item=item,
                ability=slot.get("ability"),
                nature=slot.get("stat_alignment") or slot.get("nature"),
            )
        )
    if len(members) < 2:
        return None
    record = player.get("record") or {}
    wins = int(record.get("wins") or 0)
    losses = int(record.get("losses") or 0)
    placing = player.get("placing")
    try:
        placing_i = int(placing) if placing is not None else None
    except (TypeError, ValueError):
        placing_i = None
    who = player.get("name") or player.get("Trainer name") or ""
    return Team(
        members=members,
        team_id=f"pokedata:{event_id}:{division}:{who}",
        source=f"pokedata:{kind}:{event_id}",
        weight=_event_weight(kind, placing_i, wins, players),
        wins=float(wins),
        losses=float(losses),
    )


def load_official_event(
    event_id: str,
    *,
    divisions: tuple[str, ...] = ("masters",),
) -> TeamCorpus:
    """One official event. Worlds 2026 is `WORLDS_2026_ID` ('0000191')."""
    eid = str(event_id).zfill(7) if str(event_id).isdigit() else str(event_id)
    div_path = "+".join(divisions)
    payload = _http_json(f"{POKEDATA}/apiv2/division/{div_path}/id/{eid}/vg")
    meta = payload.get("tournament") or {}
    event_name = meta.get("name") or eid
    kind = _event_kind(event_name)
    player_counts = meta.get("players") or {}
    corpus = TeamCorpus()
    for block in payload.get("tournament_data") or []:
        div = str(block.get("division") or "masters")
        if divisions and div not in divisions:
            continue
        n_players = int(player_counts.get(div) or len(block.get("data") or []))
        for player in block.get("data") or []:
            team = _team_from_pokedata_player(
                player,
                event_name=event_name,
                event_id=eid,
                kind=kind,
                players=n_players,
                division=div,
            )
            if team is not None:
                corpus.add(team)
    return corpus


def load_worlds(year: int = 2026, *, divisions: tuple[str, ...] = ("masters",)) -> TeamCorpus:
    if year == 2026:
        return load_official_event(WORLDS_2026_ID, divisions=divisions)
    events = list_official_events(year, min_decklists=1)
    worlds = [e for e in events if e["kind"] == "worlds"]
    if not worlds:
        raise ValueError(f"no Worlds event found for {year} on pokedata.ovh")
    return load_official_event(worlds[0]["id"], divisions=divisions)


def load_official_season(
    year: int = 2026,
    *,
    kinds: tuple[str, ...] = ("worlds", "international", "special", "regional"),
    min_decklists: int = 80,
    max_events: int | None = 8,
    divisions: tuple[str, ...] = ("masters",),
) -> TeamCorpus:
    """Recent official Play! events with published team lists."""
    events = [
        e
        for e in list_official_events(year, min_decklists=min_decklists)
        if e["kind"] in kinds
    ]
    if max_events is not None:
        events = events[: max_events]
    corpus = TeamCorpus()
    for ev in events:
        try:
            corpus.extend(load_official_event(ev["id"], divisions=divisions).teams)
        except Exception:
            continue
    return corpus


def list_limitless_events(*, page: int = 1, pages: int = 1) -> list[dict]:
    """Pikalytics feed of Limitless VGC cups (not official Play! events)."""
    out: list[dict] = []
    for p in range(page, page + pages):
        payload = _http_json(f"{PIKA}/api/tournaments?page={p}")
        out.extend(payload.get("tournaments") or [])
        pag = payload.get("pagination") or {}
        if not pag.get("hasNext"):
            break
    return out


def load_limitless_event(slug: str) -> TeamCorpus:
    payload = _http_json(f"{PIKA}/api/tournaments/limitless/{slug}")
    meta = payload.get("tournament") or {}
    name = meta.get("name") or slug
    players = int(meta.get("playerCount") or 0)
    corpus = TeamCorpus()
    for raw in payload.get("teams") or []:
        team = _team_from_pika_paste(raw, f"limitless:{slug}")
        if team is None:
            continue
        wins, losses = _parse_record(raw.get("record"))
        placing = raw.get("ranking") or raw.get("tournamentRanking")
        try:
            placing_i = int(placing) if placing is not None else None
        except (TypeError, ValueError):
            placing_i = None
        team.weight = _event_weight("limitless", placing_i, wins, players)
        team.wins = float(wins)
        team.losses = float(losses)
        team.team_id = str(raw.get("link") or raw.get("author") or slug)
        corpus.add(team)
    return corpus


def load_recent_limitless(
    *,
    min_players: int = 16,
    pages: int = 2,
    max_events: int = 12,
    champions_only: bool = True,
) -> TeamCorpus:
    """Finished Limitless cups large enough to matter for Reg M-B."""
    corpus = TeamCorpus()
    taken = 0
    for ev in list_limitless_events(pages=pages):
        if ev.get("isOngoing") or ev.get("status") == "ongoing":
            continue
        if int(ev.get("playerCount") or 0) < min_players:
            continue
        label = f"{ev.get('name') or ''} {ev.get('label') or ''} {ev.get('slug') or ''}"
        if champions_only and not re.search(r"champion|reg\s*m-?b|vgc", label, re.I):
            continue
        slug = ev.get("slug")
        if not slug:
            continue
        try:
            corpus.extend(load_limitless_event(slug).teams)
        except Exception:
            continue
        taken += 1
        if taken >= max_events:
            break
    return corpus


def merge_corpora(*corpora: TeamCorpus) -> TeamCorpus:
    out = TeamCorpus()
    for bag in corpora:
        if bag is not None:
            out.extend(bag.teams)
    return out


def load_teambuilder_data(
    *,
    include_worlds: bool = True,
    include_official_season: bool = False,
    include_limitless: bool = False,
    include_usage: bool = True,
    include_sample_pastes: bool = True,
) -> tuple[TeamCorpus, TeamCorpus]:
    """Composition corpus + paste corpus for `build_team` / `advise_core`.

    Official Worlds lists are full 6-mon pastes (item, ability, alignment, moves),
    so they feed both teammate recommendations and set fills.
    """
    bags: list[TeamCorpus] = []
    pastes = TeamCorpus()
    if include_usage:
        bags.append(load_team_usage(FORMAT_TOURS))
    if include_sample_pastes:
        sample = load_sample_pastes(FORMAT_TOURS)
        bags.append(sample)
        pastes.extend(sample.teams)
    if include_worlds:
        worlds = load_worlds(2026)
        bags.append(worlds)
        pastes.extend(worlds.teams)
    if include_official_season:
        season = load_official_season(2026)
        bags.append(season)
        pastes.extend(season.teams)
    if include_limitless:
        cups = load_recent_limitless()
        bags.append(cups)
        pastes.extend(cups.teams)
    return merge_corpora(*bags), pastes


# ---------------------------------------------------------------------------
# Printing / demo
# ---------------------------------------------------------------------------

def _fmt_share(pairs: list[tuple[str, float]], nd: int = 3) -> str:
    return ", ".join(f"{n} {p:.{nd}f}" for n, p in pairs) if pairs else "(none)"


def _fmt_delta(rows: list[tuple[str, float, float]]) -> str:
    return ", ".join(f"{n} {c:.2f} (Δ{d:+.2f})" for n, c, d in rows) if rows else "(none)"


def _fmt_wr(wr: float | None) -> str:
    return f"{wr:.1%}" if wr is not None else "—"


def advise_core(corpus: TeamCorpus, names: Iterable[str], *, top_n: int = 8) -> None:
    """Casual-facing dump for an arbitrary core, including off-meta ones."""
    queried = list(names)
    resolved = corpus.resolve_team(queried)
    print("Resolved core:")
    for r in resolved:
        print(f"  {r}   [{r.kind}] n={r.support:.0f}  {r.reason}" + (f"  item={r.item}" if r.item else ""))

    canonical = [r.canonical for r in resolved]
    print("\nNext picks:")
    recs = corpus.recommend_for_team(canonical, top_n=top_n)
    if not recs:
        print("  (not enough overlapping data)")
    for row in recs:
        print(
            f"  {row['pokemon']:<22} lift={row['avg_lift']:.2f}  "
            f"anchors={row['anchor_coverage']:.0%}  tog={row['avg_p_together']:.1%}  "
            f"WR={_fmt_wr(row['pair_winrate'])}  {row['confidence']}"
        )

    print("\nSets (pair-specific when the slice exists, else global fallback):")
    for mon, prof in corpus.recommend_sets_for_team(canonical).items():
        tag = f"fallback={prof.fallback}" if prof.fallback else f"slice n={prof.n_teams:.0f}"
        print(f"  {mon}  [{prof.confidence} {tag}]")
        print(f"    items: {_fmt_share(prof.top_items(4))}")
        if prof.moves:
            print(f"    moves: {_fmt_share(prof.top_moves(4))}")


def print_built_team(team: BuiltTeam) -> None:
    print("Bring-6 paste  (Stat Points = Champions EVs, Stat Alignment = Nature)")
    print("-" * 72)
    print(team.as_paste())
    print("-" * 72)
    for slot in team.slots:
        extra = f"  ({'; '.join(slot.notes)})" if slot.notes else ""
        print(f"  {slot.species:<22} {slot.confidence}{extra}")


def run_demo(
    focus: str = "Garchomp",
    partner: str = "Incineroar",
    core_partner: str = "Charizard-Mega-Y",
) -> None:
    print("=" * 72)
    print("Champions VGC  —  Limitless cups + 2026 Worlds teamlists")
    print("=" * 72)
    usage_corpus, pastes = load_teambuilder_data(include_worlds=True)
    print(
        f"loaded {len(usage_corpus)} unique 6-mon teams  "
        f"weight={usage_corpus.total_weight:.0f}  "
        f"pastes={len(pastes)}"
    )
    for raw in (focus, partner, "Chesnaught", "Charizard"):
        print(" ", usage_corpus.resolve(raw))

    print(f"\nTeammate recommendations for {focus}:")
    for row in usage_corpus.recommend_teammates(focus, top_n=8):
        print(
            f"  {row['pokemon']:<22} together={row['p_together']:.1%}  "
            f"usage={row['partner_usage']:.1%}  lift={row['lift']:.2f}  "
            f"pair WR={_fmt_wr(row['pair_winrate'])}  n={row['n']:.0f}"
        )

    core = [focus, partner]
    print(f"\nNext pick given core {core}:")
    for row in usage_corpus.recommend_for_team(core, top_n=8):
        print(
            f"  {row['pokemon']:<22} avg_lift={row['avg_lift']:.2f}  "
            f"anchors={row['anchor_coverage']:.0%}  avg_together={row['avg_p_together']:.1%}  "
            f"WR={_fmt_wr(row['pair_winrate'])}"
        )

    print("\n" + "=" * 72)
    print("Off-meta core: Chesnaught + Basculegion + Sinistcha")
    print("=" * 72)
    advise_core(usage_corpus, ["Chesnaught", "Basculegion", "Sinistcha"])

    print("\n" + "=" * 72)
    print("Full 6-slot team from that core")
    print("=" * 72)
    built = build_team(
        usage_corpus,
        ["Chesnaught", "Basculegion", "Sinistcha"],
        pastes=pastes,
    )
    print_built_team(built)

    print("\n" + "=" * 72)
    print("Worlds-winning core: Dragonite-Mega + Floette-Mega")
    print("=" * 72)
    advise_core(usage_corpus, ["Dragonite-Mega", "Floette-Mega"])


if __name__ == "__main__":
    run_demo()
