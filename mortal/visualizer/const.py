from enum import IntEnum


class TileUnitType(IntEnum):
    """Kind of a group of tiles displayed together on the board."""

    HAND = 0
    DISCARD = 1
    CHI = 2
    PON = 3
    CLOSED_KAN = 4  # ankan
    OPEN_KAN = 5  # daiminkan
    ADDED_KAN = 6  # kakan


class RelativePlayerIdx(IntEnum):
    """Seat position relative to a player."""

    SELF = 0
    RIGHT = 1  # shimocha
    CENTER = 2  # toimen
    LEFT = 3  # kamicha


class EndKind(IntEnum):
    """How the kyoku ended."""

    TSUMO = 0
    RON = 1
    RYUKYOKU = 2
