"""Board visualizer for mjai game logs.

Ported from mjx (https://github.com/mjx-project/mjx, MIT License) and
adapted to consume the mjai event format used across Mortal.

Typical usage::

    from visualizer import MahjongTable, load_mjai_log, save_svg

    events = load_mjai_log("game.json.gz")
    save_svg(events, "board.svg")                # last event of the game
    save_svg(events, "board.svg", index=42)      # board right after events[42]

or from the command line (run inside the ``mortal`` directory)::

    python -m visualizer game.json.gz --out board.svg
"""

from .const import EndKind, RelativePlayerIdx, TileUnitType
from .converter import mjai_str_to_tile_id, tile_id_to_mjai_str
from .svg import save_svg, show_svg, to_svg
from .visualizer import (
    GameBoardVisualizer,
    GameVisualConfig,
    MahjongTable,
    Player,
    Tile,
    TileUnit,
    load_mjai_log,
)

__all__ = [
    "EndKind",
    "GameBoardVisualizer",
    "GameVisualConfig",
    "MahjongTable",
    "Player",
    "RelativePlayerIdx",
    "Tile",
    "TileUnit",
    "TileUnitType",
    "load_mjai_log",
    "mjai_str_to_tile_id",
    "save_svg",
    "show_svg",
    "tile_id_to_mjai_str",
    "to_svg",
]
