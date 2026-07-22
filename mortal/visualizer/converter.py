"""Conversion tables between mjai tile strings, internal tile ids and display glyphs.

Ported from mjx (https://github.com/mjx-project/mjx, MIT License), adapted to
the mjai protocol used across Mortal.

Internal tile ids follow the 0-135 numbering used by mjx/Tenhou, where
``tile_id // 4`` is the tile type in the order m1-m9, p1-p9, s1-s9,
E, S, W, N, P (haku), F (hatsu), C (chun).  The first copy of each five
(ids 16, 52 and 88) is the red five.
"""

from __future__ import annotations

from typing import Optional

from .const import EndKind, RelativePlayerIdx, TileUnitType

# ASCII display names indexed by tile type (0-33).
to_char = [
    "m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8", "m9",
    "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9",
    "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9",
    "ew", "sw", "ww", "nw",
    "wd", "gd", "rd",
]

# Unicode mahjong tile glyphs indexed by tile type (0-33).  These code points
# are rendered as tile images by the bundled GL-MahjongTile font.
to_unicode = [
    "\U0001F007", "\U0001F008", "\U0001F009", "\U0001F00A", "\U0001F00B",
    "\U0001F00C", "\U0001F00D", "\U0001F00E", "\U0001F00F",
    "\U0001F019", "\U0001F01A", "\U0001F01B", "\U0001F01C", "\U0001F01D",
    "\U0001F01E", "\U0001F01F", "\U0001F020", "\U0001F021",
    "\U0001F010", "\U0001F011", "\U0001F012", "\U0001F013", "\U0001F014",
    "\U0001F015", "\U0001F016", "\U0001F017", "\U0001F018",
    "\U0001F000", "\U0001F001", "\U0001F002", "\U0001F003",
    "\U0001F006", "\U0001F005", "\U0001F004\uFE0E",
]

to_wind_char = [
    "EAST", "SOUTH", "WEST", "NORTH",
    "東", "南", "西", "北",
]

to_relative_player_idx = {
    RelativePlayerIdx.RIGHT: "R",
    RelativePlayerIdx.CENTER: "C",
    RelativePlayerIdx.LEFT: "L",
    RelativePlayerIdx.SELF: "S",  # closed kan
}

end_kind_en = {
    EndKind.TSUMO: "TSUMO",
    EndKind.RON: "RON",
    EndKind.RYUKYOKU: "RYUKYOKU",
}

end_kind_ja = {
    EndKind.TSUMO: "ツモ",
    EndKind.RON: "ロン",
    EndKind.RYUKYOKU: "流局",
}

# mjai tile strings indexed by tile type (0-33); red fives are handled apart.
MJAI_TILE_STRINGS = [
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C",
]

_MJAI_TO_TYPE = {s: i for i, s in enumerate(MJAI_TILE_STRINGS)}

# Red fives: mjai string -> tile id (first copy of each five).
_RED_IDS = {"5mr": 16, "5pr": 52, "5sr": 88}
RED_TILE_IDS = frozenset(_RED_IDS.values())

UNKNOWN_TILE = "?"


def mjai_str_to_tile_id(mjai_str: str) -> int:
    """Convert an mjai tile string (e.g. ``"5mr"``, ``"E"``) to a tile id.

    Red fives map to ids 16/52/88; other tiles map to a representative id of
    their type (individual copies of a tile are not distinguished by mjai).
    """
    if mjai_str in _RED_IDS:
        return _RED_IDS[mjai_str]
    tile_type = _MJAI_TO_TYPE.get(mjai_str)
    if tile_type is None:
        raise ValueError(f"unknown mjai tile string: {mjai_str!r}")
    tile_id = tile_type * 4
    if tile_id in RED_TILE_IDS:
        tile_id += 1  # plain five: avoid the red-five slot
    return tile_id


def tile_id_to_mjai_str(tile_id: int) -> str:
    if tile_id in RED_TILE_IDS:
        return {16: "5mr", 52: "5pr", 88: "5sr"}[tile_id]
    return MJAI_TILE_STRINGS[tile_id // 4]


def get_tile_char(tile_id: int, is_using_unicode: bool) -> str:
    if tile_id < 0 or tile_id > 135:
        return " "
    if is_using_unicode:
        return to_unicode[tile_id // 4]
    return to_char[tile_id // 4]


def get_wind_char(wind: int, lang: int = 0) -> str:
    if 0 <= wind < 4:
        if lang == 1:
            return to_wind_char[wind + 4]
        return to_wind_char[wind]
    return " "


def get_modifier(
    from_who: Optional[RelativePlayerIdx], tile_unit_type: TileUnitType
) -> str:
    if from_who is None:
        return ""
    if tile_unit_type == TileUnitType.ADDED_KAN:
        return to_relative_player_idx[from_who] + "(Add)"
    return to_relative_player_idx[from_who]


def get_end_kind(end_kind: EndKind, lang: int) -> str:
    if lang == 0:
        return end_kind_en[end_kind]
    return end_kind_ja[end_kind]
