"""Board state model, mjai-event decoder and terminal renderer.

Ported from mjx (https://github.com/mjx-project/mjx, MIT License).  The
board model (Tile / TileUnit / Player / MahjongTable) and the terminal
renderer are kept close to the original; the decoding layer is rewritten
to consume mjai events (the log format used across Mortal) instead of
mjxproto messages.
"""

from __future__ import annotations

import gzip
import json
import sys
from dataclasses import dataclass
from typing import IO, Any, Dict, Iterable, List, Optional, Union

from .const import EndKind, RelativePlayerIdx, TileUnitType
from .converter import (
    UNKNOWN_TILE,
    get_end_kind,
    get_modifier,
    get_tile_char,
    get_wind_char,
    mjai_str_to_tile_id,
    tile_id_to_mjai_str,
)

Event = Dict[str, Any]

_BAKAZE_TO_IDX = {"E": 0, "S": 1, "W": 2, "N": 3}


@dataclass
class GameVisualConfig:
    uni: bool = False
    rich: bool = False
    lang: int = 0  # 0: English, 1: Japanese
    show_name: bool = True


class Tile:
    """A single tile on the board plus its display state.

    ``tile_id`` follows the 0-135 numbering described in ``converter``;
    ``-1`` denotes a hidden/unknown tile (mjai ``"?"``).
    """

    def __init__(
        self,
        tile_id: int,
        is_open: bool = False,
        is_tsumogiri: bool = False,
        with_riichi: bool = False,
        highlighting: bool = False,
    ):
        assert -1 <= tile_id <= 135
        self._id = tile_id
        self.visual_char: str = "error"  # display string, set by the renderer
        self.is_open: bool = is_open and tile_id >= 0
        self.is_tsumogiri: bool = is_tsumogiri
        self.with_riichi: bool = with_riichi
        self.is_transparent: bool = False  # claimed tile, shown faded in the river
        self.is_highlighting: bool = highlighting  # marks the latest action

    @classmethod
    def from_mjai(cls, mjai_str: str, **kwargs) -> "Tile":
        if mjai_str == UNKNOWN_TILE:
            kwargs["is_open"] = False
            return cls(-1, **kwargs)
        return cls(mjai_str_to_tile_id(mjai_str), **kwargs)

    def id(self) -> int:
        return self._id

    def type(self) -> int:
        return self._id // 4 if self._id >= 0 else -1

    def is_red(self) -> bool:
        return self._id in (16, 52, 88)

    def num(self) -> Optional[int]:
        if self._id < 0 or self._id >= 108:
            return None
        return (self._id // 4) % 9 + 1

    def to_mjai(self) -> str:
        if self._id < 0:
            return UNKNOWN_TILE
        return tile_id_to_mjai_str(self._id)


class TileUnit:
    def __init__(
        self,
        tiles_type: TileUnitType,
        from_who: Optional[RelativePlayerIdx],
        tiles: List[Tile],
    ):
        self.tile_unit_type: TileUnitType = tiles_type
        self.from_who: Optional[RelativePlayerIdx] = from_who
        self.tiles: List[Tile] = tiles


class Player:
    def __init__(self, idx: int):
        self.player_idx: int = idx
        self.wind: int = 0
        self.score: str = ""
        self.tile_units: List[TileUnit] = [TileUnit(TileUnitType.DISCARD, None, [])]
        self.riichi_now: bool = False
        self.is_declared_riichi: bool = False
        self.name: str = ""
        self.draw_now: bool = False
        self.win_tile_id: int = -1

    def discard_unit(self) -> TileUnit:
        return next(
            t_u
            for t_u in self.tile_units
            if t_u.tile_unit_type == TileUnitType.DISCARD
        )


def load_mjai_log(source: Union[str, IO]) -> List[Event]:
    """Load an mjai log (one JSON event per line) from a path, ``"-"`` or a
    file object.  Gzip-compressed files (e.g. arena ``*.json.gz``) are
    detected automatically."""
    if hasattr(source, "read"):
        data = source.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
    elif source == "-":
        data = sys.stdin.buffer.read()
    else:
        with open(source, "rb") as f:
            data = f.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    events = []
    for line in data.decode("utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


class MahjongTable:
    """Holds the full state of the board (hands, rivers, melds, ...)."""

    def __init__(self):
        self.players = [Player(i) for i in range(4)]
        self.riichi: int = 0  # kyotaku deposit sticks
        self.round: int = 0  # 1-based: East 1 = 1, ..., South 1 = 5, ...
        self.honba: int = 0
        self.my_idx: int = 0  # seat shown at the bottom
        self.wall_num: int = 136
        self.doras: List[int] = []  # dora indicator tile ids
        self.uradoras: List[int] = []
        self.result: str = ""  # "", "win" or "nowinner"
        self.end_kind: Optional[EndKind] = None
        self.new_dora: Optional[int] = None
        self.latest_tile: Optional[int] = None

    def get_wall_num(self) -> int:
        all = 136 - 14
        for p in self.players:
            for t_u in p.tile_units:
                all -= len([tile for tile in t_u.tiles if not tile.is_transparent])
        return all

    def check_num_tiles(self) -> bool:
        for p in self.players:
            num_of_tiles = 0
            hand = ""
            opens = []
            for tile_unit in p.tile_units:
                if tile_unit.tile_unit_type == TileUnitType.HAND:
                    num_of_tiles += len(tile_unit.tiles)
                    hand = " ".join([tile.to_mjai() for tile in tile_unit.tiles])
                elif tile_unit.tile_unit_type != TileUnitType.DISCARD:
                    num_of_tiles += 3
                    opens.append(" ".join([tile.to_mjai() for tile in tile_unit.tiles]))

            open_str = " : ".join(opens)
            if num_of_tiles < 13 or 14 < num_of_tiles:
                sys.stderr.write(
                    f"ERROR: The number of tiles is inaccurate. Player: {p.player_idx}\n"
                    f"hand:[{hand}],open:[{open_str}]\n"
                )
                return False
        return True

    @classmethod
    def from_mjai_events(
        cls,
        events: Iterable[Event],
        index: Optional[int] = None,
        perspective: int = 0,
    ) -> "MahjongTable":
        """Decode the board state visible right after ``events[index]``.

        ``events`` is a full mjai game log (possibly several kyoku).  The
        kyoku enclosing ``index`` is replayed from its ``start_kyoku``.
        ``perspective`` picks the seat drawn at the bottom.
        """
        events = list(events)
        if not events:
            raise ValueError("empty event list")
        if index is None:
            index = len(events) - 1
        if index < 0:
            index += len(events)
        if not 0 <= index < len(events):
            raise IndexError(f"event index {index} out of range (0-{len(events) - 1})")

        start = None
        for i in range(index, -1, -1):
            if events[i].get("type") == "start_kyoku":
                start = i
                break
        if start is None:
            raise ValueError(
                f"event #{index} ({events[index].get('type')!r}) does not belong "
                "to a kyoku (no start_kyoku found before it)"
            )

        names = None
        for i in range(start, -1, -1):
            if events[i].get("type") == "start_game":
                names = events[i].get("names")
                break

        return cls._decode_kyoku(
            events[start : index + 1], names=names, perspective=perspective
        )

    @classmethod
    def _decode_kyoku(
        cls,
        events: List[Event],
        names: Optional[List[str]] = None,
        perspective: int = 0,
    ) -> "MahjongTable":
        assert events and events[0].get("type") == "start_kyoku"
        table = cls()
        table.my_idx = perspective % 4

        start_kyoku = events[0]
        bakaze = _BAKAZE_TO_IDX[start_kyoku["bakaze"]]
        table.round = bakaze * 4 + start_kyoku["kyoku"]
        table.honba = start_kyoku["honba"]
        table.riichi = start_kyoku["kyotaku"]
        oya = start_kyoku["oya"]
        table.doras = [mjai_str_to_tile_id(start_kyoku["dora_marker"])]

        scores = [int(s) for s in start_kyoku["scores"]]
        deltas = [0, 0, 0, 0]

        for i, p in enumerate(table.players):
            p.name = names[i] if names else f"player {i}"
            p.wind = (i - oya) % 4

        # concealed tiles per seat; hidden tiles ("?") have id -1
        hands: List[List[Tile]] = [
            [Tile.from_mjai(s, is_open=True) for s in start_kyoku["tehais"][seat]]
            for seat in range(4)
        ]
        last_draw: List[Optional[Tile]] = [None, None, None, None]
        # the last tile that could be claimed: ("dahai"|"kakan", seat, Tile)
        last_claim: Optional[tuple] = None
        first_hora = True

        def remove_from_hand(seat: int, mjai_str: str, prefer_drawn: bool) -> None:
            hand = hands[seat]
            drawn = last_draw[seat]
            tile_id = mjai_str_to_tile_id(mjai_str)
            if prefer_drawn and drawn is not None and drawn in hand:
                if drawn.id() == tile_id or drawn.id() == -1:
                    hand.remove(drawn)
                    return
            for tile in hand:
                if tile.id() == tile_id:
                    hand.remove(tile)
                    return
            for tile in hand:
                if tile.id() == -1:
                    hand.remove(tile)
                    return
            sys.stderr.write(
                f"WARNING: tile {mjai_str} not found in seat {seat}'s hand\n"
            )

        for j in range(1, len(events)):
            eve = events[j]
            etype = eve.get("type")
            is_last = j == len(events) - 1
            actor = eve.get("actor")

            if actor is not None:
                table.players[actor].draw_now = etype == "tsumo"

            if etype == "tsumo":
                tile = Tile.from_mjai(eve["pai"], is_open=True)
                hands[actor].append(tile)
                last_draw[actor] = tile

            elif etype == "dahai":
                p = table.players[actor]
                tsumogiri = bool(eve.get("tsumogiri", False))
                remove_from_hand(actor, eve["pai"], prefer_drawn=tsumogiri)
                tile = Tile.from_mjai(
                    eve["pai"],
                    is_open=True,
                    is_tsumogiri=tsumogiri,
                    with_riichi=p.riichi_now,
                    highlighting=is_last,
                )
                p.riichi_now = False
                p.discard_unit().tiles.append(tile)
                last_claim = ("dahai", actor, tile)
                if is_last:
                    table.latest_tile = tile.id()

            elif etype == "reach":
                table.players[actor].riichi_now = True

            elif etype == "reach_accepted":
                p = table.players[actor]
                table.riichi += 1
                scores[actor] -= 1000
                p.is_declared_riichi = True
                if is_last and p.discard_unit().tiles:
                    p.discard_unit().tiles[-1].is_highlighting = True
                    table.latest_tile = p.discard_unit().tiles[-1].id()

            elif etype in ("chi", "pon", "daiminkan"):
                p = table.players[actor]
                target = eve["target"]
                rel = RelativePlayerIdx((target - actor) % 4)
                unit_type = {
                    "chi": TileUnitType.CHI,
                    "pon": TileUnitType.PON,
                    "daiminkan": TileUnitType.OPEN_KAN,
                }[etype]

                for s in eve["consumed"]:
                    remove_from_hand(actor, s, prefer_drawn=False)

                target_river = table.players[target].discard_unit().tiles
                if target_river:
                    target_river[-1].is_transparent = True

                stolen = Tile.from_mjai(eve["pai"], is_open=True, highlighting=is_last)
                others = sorted(
                    [Tile.from_mjai(s, is_open=True) for s in eve["consumed"]],
                    key=lambda t: t.id(),
                )
                p.tile_units.append(TileUnit(unit_type, rel, [stolen] + others))
                if is_last:
                    table.latest_tile = stolen.id()

            elif etype == "ankan":
                p = table.players[actor]
                tiles = sorted(
                    [Tile.from_mjai(s, is_open=True) for s in eve["consumed"]],
                    key=lambda t: t.id(),
                )
                for s in eve["consumed"]:
                    remove_from_hand(actor, s, prefer_drawn=False)
                # the renderer shows the first and last tiles face down; keep
                # red fives in the middle so they stay visible
                reds = [t for t in tiles if t.is_red()]
                norm = [t for t in tiles if not t.is_red()]
                ordered = norm[:1] + reds + norm[1:]
                ordered[0].is_highlighting = is_last
                p.tile_units.append(
                    TileUnit(TileUnitType.CLOSED_KAN, RelativePlayerIdx.SELF, ordered)
                )
                if is_last:
                    table.latest_tile = ordered[0].id()

            elif etype == "kakan":
                p = table.players[actor]
                added = Tile.from_mjai(eve["pai"], is_open=True)
                from_who = RelativePlayerIdx.SELF
                for t_u in p.tile_units:
                    if (
                        t_u.tile_unit_type == TileUnitType.PON
                        and t_u.tiles[0].type() == added.type()
                    ):
                        if t_u.from_who is not None:
                            from_who = t_u.from_who
                        break
                p.tile_units = [
                    t_u
                    for t_u in p.tile_units
                    if not (
                        t_u.tile_unit_type == TileUnitType.PON
                        and t_u.tiles[0].type() == added.type()
                    )
                ]
                remove_from_hand(actor, eve["pai"], prefer_drawn=False)
                tiles = sorted(
                    [added]
                    + [Tile.from_mjai(s, is_open=True) for s in eve["consumed"]],
                    key=lambda t: t.id(),
                )
                tiles[0].is_highlighting = is_last
                p.tile_units.append(
                    TileUnit(TileUnitType.ADDED_KAN, from_who, tiles)
                )
                last_claim = ("kakan", actor, added)
                if is_last:
                    table.latest_tile = tiles[0].id()

            elif etype == "dora":
                dora_id = mjai_str_to_tile_id(eve["dora_marker"])
                table.doras.append(dora_id)
                table.new_dora = dora_id

            elif etype == "hora":
                table.result = "win"
                winner = table.players[actor]
                target = eve.get("target", actor)
                if actor == target:  # tsumo
                    table.end_kind = EndKind.TSUMO
                    winner.draw_now = True
                    if last_draw[actor] is not None:
                        winner.win_tile_id = last_draw[actor].id()
                        last_draw[actor].is_highlighting = True
                else:  # ron
                    table.end_kind = EndKind.RON
                    if last_claim is not None:
                        claim_kind, claim_seat, claim_tile = last_claim
                        winner.win_tile_id = claim_tile.id()
                        if first_hora and claim_kind == "dahai":
                            claim_tile.is_transparent = True
                        elif first_hora and claim_kind == "kakan":
                            # chankan: the added tile goes to the winner, so
                            # the kan reverts to the original pon
                            for t_u in table.players[claim_seat].tile_units:
                                if (
                                    t_u.tile_unit_type == TileUnitType.ADDED_KAN
                                    and claim_tile in t_u.tiles
                                ):
                                    t_u.tiles.remove(claim_tile)
                                    t_u.tile_unit_type = TileUnitType.PON
                                    break
                        win_tile = Tile(
                            claim_tile.id(), is_open=True, highlighting=True
                        )
                        hands[actor].append(win_tile)
                        last_draw[actor] = win_tile
                        winner.draw_now = True
                if eve.get("deltas"):
                    for i, d in enumerate(eve["deltas"]):
                        deltas[i] += d
                if eve.get("ura_markers"):
                    table.uradoras = [
                        mjai_str_to_tile_id(s) for s in eve["ura_markers"]
                    ]
                first_hora = False

            elif etype == "ryukyoku":
                table.result = "nowinner"
                table.end_kind = EndKind.RYUKYOKU
                if eve.get("deltas"):
                    for i, d in enumerate(eve["deltas"]):
                        deltas[i] += d
                if is_last and last_claim is not None and last_claim[0] == "dahai":
                    last_claim[2].is_highlighting = True
                    table.latest_tile = last_claim[2].id()

            elif etype in ("end_kyoku", "end_game", "none", "start_game"):
                pass

            else:
                sys.stderr.write(f"WARNING: unknown mjai event type {etype!r}\n")

            if etype != "dora":
                table.new_dora = None

        # assemble the concealed-hand tile units
        for seat in range(4):
            p = table.players[seat]
            hand = hands[seat]
            drawn = last_draw[seat]
            if p.draw_now and drawn is not None and drawn in hand:
                rest = sorted(
                    [t for t in hand if t is not drawn], key=lambda t: t.id()
                )
                tiles = rest + [drawn]
                if drawn.id() >= 0:
                    table.latest_tile = drawn.id()
            else:
                tiles = sorted(hand, key=lambda t: t.id())
            p.tile_units.append(TileUnit(TileUnitType.HAND, None, tiles))

        # score display, with deltas at the end of the kyoku
        for i, p in enumerate(table.players):
            if table.result:
                delta = deltas[i]
                p.score = (
                    str(scores[i] + delta)
                    + ("(+" if delta > 0 else "(")
                    + str(delta)
                    + ")"
                )
            else:
                p.score = str(scores[i])

        table.wall_num = table.get_wall_num()
        return table


class GameBoardVisualizer:
    """Terminal renderer (plain text or rich) for a MahjongTable."""

    def __init__(self, config: GameVisualConfig):
        self.config = config

    def get_layout(self):
        from rich.layout import Layout

        layout = Layout()
        if self.config.show_name:
            layout.split_column(
                Layout(" ", name="space_top"),
                Layout(name="info"),
                Layout(name="players_info_top"),
                Layout(name="table"),
            )
            layout["players_info_top"].size = 7
            layout["players_info_top"].split_row(
                Layout(" ", name="player1_info_top"),
                Layout(" ", name="player2_info_top"),
                Layout(" ", name="player3_info_top"),
                Layout(" ", name="player4_info_top"),
            )
        else:
            layout.split_column(
                Layout(" ", name="space_top"),
                Layout(name="info"),
                Layout(name="table"),
            )

        layout["space_top"].size = 3
        layout["info"].size = 3
        layout["table"].minimum_size = 20

        layout["table"].split_column(
            Layout(name="upper1"),
            Layout(name="middle1"),
            Layout(name="lower1"),
        )

        layout["upper1"].size = 3
        layout["lower1"].size = 3

        layout["upper1"].split_row(
            Layout(" "),
            Layout(" ", name="hand3"),
            Layout(" "),
        )
        layout["hand3"].ratio = 6

        layout["middle1"].split_row(
            Layout(" ", name="hand4"),
            Layout(name="middle2"),
            Layout(" ", name="hand2"),
        )
        layout["middle2"].ratio = 10

        layout["lower1"].split_row(
            Layout(" "),
            Layout(" ", name="hand1"),
            Layout(" "),
        )
        layout["hand1"].ratio = 6

        layout["middle2"].split_column(
            Layout(name="upper2"),
            Layout(name="middle3"),
            Layout(name="lower2"),
        )

        layout["upper2"].split_row(
            Layout(" ", name="player3_info_corner"),
            Layout(" ", name="discard3"),
            Layout(" ", name="player2_info_corner"),
        )

        layout["middle3"].split_row(
            Layout(" ", name="discard4"),
            Layout(" ", name="middle4"),
            Layout(" ", name="discard2"),
        )

        layout["middle4"].split_column(
            Layout(" ", name="space_for_info_center"),
            Layout(" ", name="player3_info_center"),
            Layout(" ", name="middle5"),
            Layout(" ", name="player1_info_center"),
        )
        layout["space_for_info_center"].size = 1

        layout["middle5"].split_row(
            Layout(" ", name="player4_info_center"),
            Layout(" ", name="player2_info_center"),
        )
        layout["lower2"].split_row(
            Layout(" ", name="player4_info_corner"),
            Layout(" ", name="discard1"),
            Layout(" ", name="player1_info_corner"),
        )

        return layout

    def add_suffix(self, tile_unit: TileUnit, player_idx: int = 0) -> str:
        for tile in tile_unit.tiles:
            if not tile.is_open:
                tile.visual_char = "\U0001F02B" if self.config.uni else "#"
            else:
                tile.visual_char = get_tile_char(tile.id(), self.config.uni)
            if tile.is_tsumogiri:
                if tile.with_riichi:
                    if self.config.uni and tile.visual_char != "\U0001F004\uFE0E":
                        tile.visual_char += " *r"
                    else:
                        tile.visual_char += "*r"
                else:
                    if self.config.uni and tile.visual_char != "\U0001F004\uFE0E":
                        tile.visual_char += " *"
                    else:
                        tile.visual_char += "*"
            elif tile.with_riichi:
                if self.config.uni and tile.visual_char != "\U0001F004\uFE0E":
                    tile.visual_char += " r"
                else:
                    tile.visual_char += "r"

        if self.config.rich:
            if tile_unit.tile_unit_type == TileUnitType.DISCARD:
                discards = [
                    tile.visual_char
                    + (
                        ""
                        if (
                            tile.visual_char == "\U0001F004\uFE0E"
                            or (tile.is_tsumogiri and tile.with_riichi)
                        )
                        else " "
                    )
                    + (
                        ""
                        if (tile.is_tsumogiri or tile.with_riichi)
                        else "  "
                        if self.config.uni
                        else " "
                    )
                    for tile in tile_unit.tiles
                ]
                tiles = "\n".join(
                    ["".join(discards[idx : idx + 6]) for idx in range(0, len(discards), 6)]
                )
                return tiles
            elif player_idx == 1:
                tiles = (
                    "\n"
                    + get_modifier(tile_unit.from_who, tile_unit.tile_unit_type)
                    + "\n"
                    + "\n".join(
                        [
                            (" " if tile.visual_char == "\U0001F004\uFE0E" else "")
                            + tile.visual_char
                            for tile in tile_unit.tiles
                        ]
                    )
                    + "\n"
                )
                return tiles
            elif player_idx == 2:
                if tile_unit.tile_unit_type == TileUnitType.HAND:
                    tiles = "".join(
                        [
                            tile.visual_char
                            + ("" if tile.visual_char == "\U0001F004\uFE0E" else " ")
                            for tile in tile_unit.tiles
                        ]
                    )
                    return tiles
                tiles = get_modifier(tile_unit.from_who, tile_unit.tile_unit_type) + "".join(
                    [
                        tile.visual_char
                        + (
                            ""
                            if (not self.config.uni or tile.visual_char == "\U0001F004\uFE0E")
                            else " "
                        )
                        for tile in sorted(
                            tile_unit.tiles,
                            key=lambda x: x.id(),
                            reverse=True,
                        )
                    ]
                )
                return tiles
            elif player_idx == 3:
                tiles = (
                    "\n"
                    + "\n".join(
                        [
                            (" " if tile.visual_char == "\U0001F004\uFE0E" else "")
                            + tile.visual_char
                            for tile in tile_unit.tiles
                        ]
                    )
                    + "\n "
                    + get_modifier(tile_unit.from_who, tile_unit.tile_unit_type)
                    + "\n"
                )
                return tiles
            else:
                if tile_unit.tile_unit_type == TileUnitType.HAND:
                    tiles = "".join(
                        [
                            tile.visual_char
                            + ("" if tile.visual_char == "\U0001F004\uFE0E" else " ")
                            for tile in tile_unit.tiles
                        ]
                    )
                    return tiles
                tiles = (
                    "".join(
                        [
                            tile.visual_char
                            + (
                                ""
                                if (not self.config.uni or tile.visual_char == "\U0001F004\uFE0E")
                                else " "
                            )
                            for tile in tile_unit.tiles
                        ]
                    )
                    + get_modifier(tile_unit.from_who, tile_unit.tile_unit_type)
                    + " "
                )
                return tiles
        else:  # not rich
            if tile_unit.tile_unit_type == TileUnitType.HAND:
                tiles = "".join(
                    [
                        tile.visual_char + ("" if tile.visual_char == "\U0001F004\uFE0E" else " ")
                        for tile in tile_unit.tiles
                    ]
                )
                return tiles
            if tile_unit.tile_unit_type == TileUnitType.DISCARD:
                discards = [
                    tile.visual_char
                    + (
                        ""
                        if (
                            tile.visual_char == "\U0001F004\uFE0E"
                            or (tile.is_tsumogiri and tile.with_riichi)
                        )
                        else " "
                    )
                    + (
                        ""
                        if (tile.is_tsumogiri or tile.with_riichi)
                        else "  "
                        if self.config.uni
                        else " "
                    )
                    for tile in tile_unit.tiles
                ]
                tiles = "\n".join(
                    ["".join(discards[idx : idx + 6]) for idx in range(0, len(discards), 6)]
                )
                return tiles

            if tile_unit.tile_unit_type in [
                TileUnitType.CHI,
                TileUnitType.PON,
                TileUnitType.CLOSED_KAN,
                TileUnitType.OPEN_KAN,
                TileUnitType.ADDED_KAN,
            ]:
                tiles = "".join(
                    [
                        tile.visual_char
                        + (
                            ""
                            if (not self.config.uni or tile.visual_char == "\U0001F004\uFE0E")
                            else " "
                        )
                        for tile in tile_unit.tiles
                    ]
                ) + get_modifier(tile_unit.from_who, tile_unit.tile_unit_type)
                return tiles
        return "error"

    def get_board_info(self, table: MahjongTable) -> str:
        board_info = []
        board_info.append(
            [
                f"round:{table.round}",
                get_wind_char((table.round - 1) // 4, self.config.lang)
                + str((table.round - 1) % 4 + 1)
                + "局",
            ][self.config.lang]
        )
        if table.honba > 0:
            board_info.append(
                " " + ["honba:" + str(table.honba), str(table.honba) + "本場"][self.config.lang]
            )
        if table.riichi > 0:
            board_info.append(" " + ["riichi:", "供託"][self.config.lang] + str(table.riichi))
        board_info.append(
            " "
            + ["wall:" + str(table.wall_num), "残り:" + str(table.wall_num) + "枚"][self.config.lang]
        )
        dora = "".join(
            [
                get_tile_char(d, self.config.uni)
                + ("" if get_tile_char(d, self.config.uni) == "\U0001F004\uFE0E" else " ")
                for d in table.doras
            ]
        )
        board_info.append(" " + ["Dora:", "ドラ:"][self.config.lang] + dora)
        uradora = "".join(
            [
                get_tile_char(d, self.config.uni)
                + ("" if get_tile_char(d, self.config.uni) == "\U0001F004\uFE0E" else " ")
                for d in table.uradoras
            ]
        )
        if uradora != "":
            board_info.append(" " + ["UraDora:", "裏ドラ:"][self.config.lang] + uradora)

        if table.end_kind is not None:
            board_info.append("    " + get_end_kind(table.end_kind, self.config.lang))

        return "".join(board_info)

    def get_text_width(self, text: str):
        import unicodedata

        text_counter: int = 0
        for c in text:
            if unicodedata.east_asian_width(c) in "FWA":
                text_counter = text_counter + 2
            else:
                text_counter = text_counter + 1
        return text_counter

    def show_by_text(self, table: MahjongTable) -> str:
        board_info = self.get_board_info(table)
        board_info = (
            "#" * (self.get_text_width(board_info) + 2)
            + "\n"
            + "#"
            + board_info
            + "#\n"
            + "#" * (self.get_text_width(board_info) + 2)
            + "\n"
        )

        players_info = []
        table.players.sort(key=lambda x: (x.player_idx - table.my_idx) % 4)
        for i, p in enumerate(table.players):
            player_info = []

            player_info.append(
                get_wind_char(p.wind, self.config.lang)
                + " [ "
                + "".join(
                    [
                        p.score
                        + ([", riichi", ", リーチ"][self.config.lang] if p.is_declared_riichi else "")
                    ]
                )
                + " ]",
            )
            if self.config.show_name:
                player_info.append(" " + p.name)
            player_info.append("\n\n")

            opens = []
            hand = ""
            discards = ""
            for t_u in reversed(p.tile_units):
                if t_u.tile_unit_type == TileUnitType.HAND:
                    hand = self.add_suffix(t_u)
                elif t_u.tile_unit_type == TileUnitType.DISCARD:
                    discards = self.add_suffix(t_u)
                else:
                    opens.append(self.add_suffix(t_u))

            hand_area = hand + "      " + " ".join(opens)
            player_info.append(hand_area)
            player_info.append("\n\n")

            player_info.append(discards)
            player_info.append("\n\n\n")
            players_info.append("".join(player_info))

        return board_info + "".join(players_info)

    def show_by_rich(self, table: MahjongTable) -> None:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        layout = self.get_layout()

        layout["info"].update(
            Panel(
                Text(self.get_board_info(table), justify="center", style="color(1)"),
                style="bold green",
            )
        )

        table.players.sort(key=lambda x: (x.player_idx - table.my_idx) % 4)

        players_info_top = [
            "player1_info_top",
            "player2_info_top",
            "player3_info_top",
            "player4_info_top",
        ]
        players_info_center = [
            "player1_info_center",
            "player2_info_center",
            "player3_info_center",
            "player4_info_center",
        ]
        hands_idx = ["hand1", "hand2", "hand3", "hand4"]
        discards_idx = [
            "discard1",
            "discard2",
            "discard3",
            "discard4",
        ]

        for i, p in enumerate(table.players):
            wind = Text(
                get_wind_char(p.wind, self.config.lang),
                justify="center",
                style="bold green",
            )

            score = Text(p.score, justify="center", style="yellow")

            riichi = Text()
            if p.is_declared_riichi:
                riichi = [
                    Text(" riichi", style="yellow"),
                    Text(" リーチ", style="yellow"),
                ][self.config.lang]

            player_info = wind + Text("\n") + score + riichi

            layout[players_info_center[i]].update(player_info)

            name = Text(justify="center", style="bold green")
            if self.config.show_name:
                name += Text(" " + p.name, style="white")
                layout[players_info_top[i]].update(
                    Panel(player_info + Text("\n\n") + name, style="bold green")
                )

            opens = []
            hand = ""
            discards = ""
            for t_u in reversed(p.tile_units):
                if t_u.tile_unit_type == TileUnitType.HAND:
                    hand = self.add_suffix(t_u, player_idx=i)
                elif t_u.tile_unit_type == TileUnitType.DISCARD:
                    discards = self.add_suffix(t_u, player_idx=i)
                else:
                    opens.append(self.add_suffix(t_u, player_idx=i))
            if p.player_idx in [(table.my_idx + 1) % 4, (table.my_idx + 2) % 4]:
                hand_area = " ".join(opens) + "      " + hand
            else:
                hand_area = hand + "      " + " ".join(opens)
            layout[hands_idx[i]].update(
                Panel(
                    Text(hand_area, justify="center", no_wrap=True, style="white"),
                    style="bold green",
                )
            )

            layout[discards_idx[i]].update(
                Panel(
                    Text(
                        discards,
                        justify="left",
                        style="white",
                    ),
                    style="bold green",
                )
            )

        console = Console()
        console.print(layout)

    def print(self, data: MahjongTable):
        if self.config.rich:
            self.show_by_rich(data)
        else:
            print(self.show_by_text(data))
