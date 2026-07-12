"""Interactive whole-game move-evaluation report for mjai logs.

Renders every board state of a game as SVG frames (via :func:`svg.to_svg`)
and overlays the engine's own evaluation — decoded from the
``meta.q_values`` / ``meta.mask_bits`` fields Mortal writes into its mjai
logs — directly onto the board: each candidate discard gets its policy
probability written on the tile in hand, call options (chi/pon/kan/riichi/
agari/pass) become chips above the hand with the tiles they would use, and
the engine's top choice is framed.  Decision frames show the board at
decision time, i.e. right before the evaluated action was taken.  The
output is a single self-contained HTML file with a timeline to flip
through the whole game kyoku by kyoku.

Only decisions already evaluated in the log are annotated.  Mortal's arena
writes ``meta`` for every engine-driven seat; for logs from elsewhere the
engine can be replayed with ``MORTAL_REVIEW_MODE=1 python mortal.py <seat>``
(see ``mortal.py``) — merging that output is not implemented here yet.
"""

from __future__ import annotations

import json
import math
import re
from importlib.resources import files
from typing import Any, Dict, List, Optional, Tuple

from .converter import get_tile_char, mjai_str_to_tile_id
from .visualizer import Event, MahjongTable

__all__ = ["decode_candidates", "save_review_html", "to_review_html"]

ACTION_SPACE = 46  # keep in sync with libriichi/src/consts.rs

# action indices 0-36 in libriichi's order (libriichi/src/tile.rs)
_MJAI_37 = [
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C", "5mr", "5pr", "5sr",
]
_SPECIAL = {
    37: "立直", 38: "吃", 39: "吃", 40: "吃",
    41: "碰", 42: "槓", 43: "和", 44: "流局", 45: "過",
}
_AKA_KIND = {34: 4, 35: 13, 36: 22}  # red five action -> tile kind

# bottom-player hand layout; keep in sync with svg.py (left_margin,
# char_width and the hand y in the untransformed bottom group)
_LEFT_MARGIN = 190
_CHAR_WIDTH = 32

_BAKAZE = {"E": "東", "S": "南", "W": "西", "N": "北"}

_INK1, _INK2, _INK3 = "#0b0b0b", "#52514e", "#898781"
_BLUE, _TRACK = "#2a78d6", "#e4e1d7"


def decode_candidates(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Expand compact ``meta`` into candidates sorted by Q value.

    ``meta["q_values"]`` holds Q values of the legal actions only; bit ``i``
    of ``meta["mask_bits"]`` marks action ``i`` of the 46-action space as
    legal, and the k-th set bit pairs with ``q_values[k]``.  ``p`` is the
    softmax (temperature 1) over the legal Q values.
    """
    qs = meta["q_values"]
    bits = meta["mask_bits"]
    idxs = [i for i in range(ACTION_SPACE) if bits >> i & 1]
    if len(idxs) != len(qs):
        raise ValueError(
            f"mask_bits has {len(idxs)} legal actions but got {len(qs)} q_values"
        )
    mx = max(qs)
    ws = [math.exp(q - mx) for q in qs]
    tot = sum(ws)
    cands = [{"idx": i, "q": q, "p": w / tot} for i, q, w in zip(idxs, qs, ws)]
    cands.sort(key=lambda c: -c["q"])
    return cands


def _action_index_of(event: Event) -> Optional[int]:
    """The 46-action-space index the logged event corresponds to."""
    kind = event["type"]
    if kind == "dahai":
        return _MJAI_37.index(event["pai"])
    if kind == "chi":
        low = sorted(mjai_str_to_tile_id(p) // 4 for p in event["consumed"])
        pai = mjai_str_to_tile_id(event["pai"]) // 4
        return 38 if pai < low[0] else (39 if low[0] < pai < low[1] else 40)
    if kind in ("ankan", "kakan"):
        # the kan-select stage reuses tile indices; the decide stage is 42
        return _MJAI_37.index(event["pai"]) if "pai" in event else 42
    return {
        "pon": 41, "daiminkan": 42, "hora": 43, "ryukyoku": 44, "reach": 37,
    }.get(kind)


def _claimed_pai(events: List[Event], index: int) -> Optional[str]:
    """The tile a claim at ``index`` would take (last discard/kakan)."""
    for j in range(index - 1, -1, -1):
        kind = events[j]["type"]
        if kind in ("dahai", "kakan"):
            return events[j]["pai"]
        if kind == "start_kyoku":
            return None
    return None


def _glyph(mjai_str: str) -> str:
    return get_tile_char(mjai_str_to_tile_id(mjai_str), True)


def _candidate_display(
    idx: int, claimed: Optional[str], kan_choice: bool
) -> Tuple[str, str, bool]:
    """(word, tile glyphs, is_red) describing a candidate action."""
    if idx < 37:
        pai = _MJAI_37[idx]
        return ("槓" if kan_choice else "打"), _glyph(pai), pai.endswith("r")
    if idx in (38, 39, 40) and claimed:
        kind = mjai_str_to_tile_id(claimed) // 4
        pair = {
            38: (kind + 1, kind + 2),
            39: (kind - 1, kind + 1),
            40: (kind - 2, kind - 1),
        }[idx]
        return "吃", "".join(_glyph(_MJAI_37[k]) for k in pair), False
    if idx == 41 and claimed:
        return "碰", _glyph(claimed) * 2, False
    if idx == 42 and claimed:
        return "槓", _glyph(claimed) * 3, False
    return _SPECIAL[idx], "", False


def _seat_hand(events: List[Event], index: int, seat: int):
    """Hand tiles of ``seat`` (drawing order) right after ``events[index]``."""
    from .const import TileUnitType

    table = MahjongTable.from_mjai_events(events, index=index, perspective=seat)
    player = next(p for p in table.players if p.player_idx == seat)
    return [
        tile
        for unit in player.tile_units
        if unit.tile_unit_type == TileUnitType.HAND
        for tile in unit.tiles
    ]


def _tile_pos(hand, idx: int) -> Optional[int]:
    """Rightmost hand position holding the tile of discard action ``idx``."""
    if idx in _AKA_KIND:
        kind, red = _AKA_KIND[idx], True
    else:
        kind, red = idx, False
    pos = None
    for j, tile in enumerate(hand):
        if tile.id() // 4 == kind and tile.is_red() == red:
            pos = j
    return pos


def _fmt_pct(p: float) -> str:
    v = p * 100
    return "<1%" if v < 0.5 else f"{v:.0f}%"


def _overlay_svg(
    cands: List[Dict[str, Any]],
    hand,
    ev_index: int,
    claimed: Optional[str],
    kan_choice: bool,
) -> str:
    """SVG markup drawing the evaluation onto the board."""
    parts = [
        '<g style="font-family:system-ui,-apple-system,sans-serif;">',
        f'<text x="{_LEFT_MARGIN}" y="666" font-size="12" fill="{_INK3}">'
        f"決策時點 — 事件 #{ev_index} 執行前</text>",
    ]
    chip_x = _LEFT_MARGIN
    best = cands[0]["idx"]
    for c in cands:
        on_tile = c["idx"] < 37 and not kan_choice
        j = _tile_pos(hand, c["idx"]) if on_tile else None
        is_best = c["idx"] == best
        if j is not None:
            x = _LEFT_MARGIN + j * _CHAR_WIDTH
            parts.append(
                f'<rect x="{x + 1}" y="777" width="30" height="5" rx="2" fill="{_TRACK}"/>'
            )
            w = max(30 * c["p"], 1.5)
            parts.append(
                f'<rect x="{x + 1}" y="777" width="{w:.1f}" height="5" rx="2" fill="{_BLUE}"/>'
            )
            parts.append(
                f'<text x="{x + 15}" y="719" font-size="12" text-anchor="middle" '
                f'font-weight="{700 if is_best else 400}" '
                f'fill="{_INK1 if is_best else _INK2}">{_fmt_pct(c["p"])}</text>'
            )
            if is_best:
                parts.append(
                    f'<rect x="{x - 1}" y="725" width="34" height="49" rx="4" '
                    f'fill="none" stroke="{_BLUE}" stroke-width="2.5"/>'
                )
        else:
            word, tiles, _ = _candidate_display(c["idx"], claimed, kan_choice)
            pct = _fmt_pct(c["p"])
            w = 24 + 13 * len(word) + 16 * len(tiles) + 8 * len(pct)
            fill = _BLUE if is_best else "#f1efe9"
            text_fill = "#ffffff" if is_best else _INK1
            stroke = _BLUE if is_best else "#c3c2b7"
            tile_span = (
                f'<tspan font-family="GL-MahjongTile" font-size="24">{tiles}</tspan>'
                if tiles
                else ""
            )
            parts.append(
                f'<rect x="{chip_x}" y="678" width="{w}" height="30" rx="15" '
                f'fill="{fill}" stroke="{stroke}"/>'
            )
            parts.append(
                f'<text x="{chip_x + 12}" y="699" font-size="13" '
                f'font-weight="{700 if is_best else 400}" fill="{text_fill}">'
                f"{word} {tile_span} {pct}</text>"
            )
            chip_x += w + 8
    parts.append("</g>")
    return "".join(parts)


_FONT_RE = re.compile(
    r"<defs><style[^>]*><!\[CDATA\[(.*?)\]\]></style></defs>", re.S
)
_ROOT_RE = re.compile(
    r'<svg baseProfile="full" height="800" version="1.1" width="800"'
)


def _strip_svg(svg: str) -> Tuple[str, Optional[str]]:
    """Drop the per-file font/defs and fixed size; return (svg, font css)."""
    font_css = None
    m = _FONT_RE.search(svg)
    if m:
        font_css = m.group(1)
        svg = svg[: m.start()] + svg[m.end() :]
    svg = _ROOT_RE.sub('<svg viewBox="0 0 800 800" class="board-svg"', svg, count=1)
    return svg.replace('<?xml version="1.0" encoding="utf-8" ?>', "", 1), font_css


def _describe(event: Event, names: List[str]) -> Tuple[str, str]:
    kind = event["type"]
    actor = event.get("actor")
    who = f"{names[actor]}(座{actor})" if actor is not None else ""
    words = {
        "start_kyoku": "配牌", "tsumo": "摸牌",
        "dahai": "打 " + event.get("pai", ""),
        "chi": "吃 " + event.get("pai", ""), "pon": "碰 " + event.get("pai", ""),
        "daiminkan": "大明槓", "ankan": "暗槓", "kakan": "加槓",
        "reach": "立直宣言", "reach_accepted": "立直成立", "dora": "新寶牌",
        "ryukyoku": "流局", "end_kyoku": "局終", "end_game": "終局",
    }
    if kind == "hora":
        desc = "自摸和" if event.get("actor") == event.get("target") else "榮和"
    else:
        desc = words.get(kind, kind)
    if event.get("tsumogiri"):
        desc += "(摸切)"
    return who, desc


def _kyoku_label(event: Event) -> str:
    label = f"{_BAKAZE.get(event['bakaze'], event['bakaze'])}{event['kyoku']}局"
    if event.get("honba"):
        label += f" {event['honba']}本場"
    return label


def _detect_actor(events: List[Event]) -> int:
    counts = [0, 0, 0, 0]
    for e in events:
        if "q_values" in e.get("meta", {}) and e.get("actor") is not None:
            counts[e["actor"]] += 1
    if not any(counts):
        raise ValueError(
            "no meta.q_values found in this log for any seat; "
            "this log carries no engine evaluations to review"
        )
    return max(range(4), key=lambda i: counts[i])


def to_review_html(events: List[Event], actor: Optional[int] = None) -> str:
    """Build the review page for ``actor`` (autodetected if ``None``)."""
    from .svg import to_svg

    events = list(events)
    if actor is None:
        actor = _detect_actor(events)
    if not 0 <= actor <= 3:
        raise ValueError(f"actor must be within [0, 3], got {actor}")

    start_game = next((e for e in events if e["type"] == "start_game"), {})
    names = start_game.get("names", [f"player {i}" for i in range(4)])

    first = next(
        (i for i, e in enumerate(events) if e["type"] == "start_kyoku"), None
    )
    if first is None:
        raise ValueError("log contains no start_kyoku event")

    kyokus: List[Dict[str, Any]] = []
    frames: List[Dict[str, Any]] = []
    total = matches = 0

    for i in range(first, len(events)):
        event = events[i]
        if event["type"] == "start_kyoku":
            kyokus.append({"label": _kyoku_label(event), "a": len(frames)})
        meta = event.get("meta", {})
        is_decision = (
            "q_values" in meta and event.get("actor") == actor and i > first
        )

        who, desc = _describe(event, names)
        frame: Dict[str, Any] = {
            "i": i,
            # decision frames show the board at decision time (pre-action)
            "board": i - 1 if is_decision else i,
            "who": who,
            "desc": desc,
            "hora": event["type"] == "hora",
            "eval": None,
            "overlay": "",
        }
        if is_decision:
            cands = decode_candidates(meta)
            taken = _action_index_of(event)
            kan_choice = event["type"] in ("ankan", "kakan") and cands[0]["idx"] < 37
            claimed = _claimed_pai(events, i)
            hand = _seat_hand(events, i - 1, actor)
            frame["overlay"] = _overlay_svg(cands, hand, i, claimed, kan_choice)

            total += 1
            dev = taken is not None and taken != cands[0]["idx"]
            matches += 0 if dev else 1
            out = []
            for c in cands:
                word, tiles, aka = _candidate_display(c["idx"], claimed, kan_choice)
                if c["idx"] < 37:
                    word += " " + _MJAI_37[c["idx"]]
                out.append(
                    {
                        "label": word,
                        "glyph": tiles,
                        "aka": aka,
                        "q": round(c["q"], 4),
                        "p": round(c["p"], 4),
                        "taken": c["idx"] == taken,
                    }
                )
            frame["eval"] = {
                "cands": out,
                "dev": dev,
                "greedy": meta.get("is_greedy"),
                "shanten": meta.get("shanten"),
                "furiten": meta.get("at_furiten"),
            }
        frames.append(frame)

    for n, kyoku in enumerate(kyokus):
        kyoku["b"] = kyokus[n + 1]["a"] - 1 if n + 1 < len(kyokus) else len(frames) - 1

    # render each referenced board exactly once
    needed = sorted({frame["board"] for frame in frames})
    board_pos = {idx: n for n, idx in enumerate(needed)}
    font_css: Optional[str] = None
    boards: List[str] = []
    for idx in needed:
        svg, css = _strip_svg(to_svg(events, index=idx, target_idx=actor))
        font_css = font_css or css
        boards.append(svg)
    for frame in frames:
        frame["board"] = board_pos[frame["board"]]

    rate = f"{matches}/{total} = {matches / total * 100:.1f}%" if total else "—"
    template = files(__package__).joinpath("review_template.html").read_text("utf-8")
    return (
        template.replace("__FONT_CSS__", font_css or "")
        .replace("__TITLE__", f"Mortal 牌譜檢討 — {names[actor]}(座{actor})")
        .replace("__SEAT__", f"{names[actor]}(座{actor})")
        .replace("__RATE__", rate)
        .replace("__KYOKUS__", json.dumps(kyokus, ensure_ascii=False))
        .replace("__DATA__", json.dumps(frames, ensure_ascii=False))
        .replace(
            "__BOARDS__",
            "\n".join(
                f'<div class="frame" data-b="{n}">{svg}</div>'
                for n, svg in enumerate(boards)
            ),
        )
    )


def save_review_html(
    events: List[Event], filename: str = "review.html", actor: Optional[int] = None
) -> None:
    """Write the interactive review page for ``events`` to ``filename``."""
    html = to_review_html(events, actor=actor)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
