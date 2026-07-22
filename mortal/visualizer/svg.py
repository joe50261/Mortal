"""SVG board renderer.

Ported from mjx (https://github.com/mjx-project/mjx, MIT License).  The
whole coordinate layout is unchanged; the input is a decoded
``MahjongTable`` (built from mjai events) instead of an mjxproto message.

Tiles are drawn as Unicode mahjong glyphs rendered by the embedded
GL-MahjongTile font, so the output SVG is fully self-contained.
"""

from __future__ import annotations

import base64
from importlib.resources import files
from typing import List, Optional, Tuple, Union

import svgwrite
from svgwrite.drawing import Drawing

from .const import RelativePlayerIdx, TileUnitType
from .converter import get_tile_char, get_wind_char
from .visualizer import Event, MahjongTable, Tile, load_mjai_log


def _read_asset(name: str) -> bytes:
    return files(__package__).joinpath(name).read_bytes()


def dwg_add(
    dwg_p,
    dwg_g,
    pos,
    txt: str,
    rotate: bool = False,
    is_red: bool = False,
    transparent: bool = False,
    highliting: bool = False,
):
    opacity = 1.0
    if transparent:
        opacity = 0.5

    highlight_fill = "black"
    highlight_opacity = 1.0
    highlight_stroke_width = 2.0

    if rotate:
        if is_red:
            horizontal_tiles = [
                dwg_p.text(txt[0], insert=(0, 0), fill="red", opacity=opacity),
                dwg_p.text(
                    "\U0001F006",
                    insert=(0, 0),
                    stroke=svgwrite.rgb(255, 255, 255, "%"),
                    fill="white",
                ),
                dwg_p.text("\U0001F006", insert=(0, 0), fill="black", opacity=opacity),
            ]

            for horizontal_tile in horizontal_tiles:
                horizontal_tile.rotate(90, (0, 0))
                horizontal_tile.translate(pos)
                dwg_g.add(horizontal_tile)

        else:
            horizontal_tile = dwg_p.text(txt[0], insert=(0, 0), opacity=opacity)
            horizontal_tile.rotate(90, (0, 0))
            horizontal_tile.translate(pos)
            dwg_g.add(horizontal_tile)

        if highliting:
            highlighted_tile = dwg_p.text(
                "\U0001F006",
                insert=(0, 0),
                stroke=svgwrite.rgb(0, 0, 0, "%"),
                stroke_width=highlight_stroke_width,
                fill=highlight_fill,
                opacity=highlight_opacity,
            )
            highlighted_tile.rotate(90, (0, 0))
            highlighted_tile.translate(pos)
            dwg_g.add(highlighted_tile)

    else:
        if is_red:
            dwg_g.add(dwg_p.text(txt[0], pos, fill="red", opacity=opacity))
            dwg_g.add(
                dwg_p.text(
                    "\U0001F006",
                    pos,
                    stroke=svgwrite.rgb(255, 255, 255, "%"),
                    fill="white",
                    opacity=opacity,
                )
            )
            dwg_g.add(dwg_p.text("\U0001F006", pos, fill="black", opacity=opacity))
        else:
            dwg_g.add(dwg_p.text(txt[0], pos, opacity=opacity))

        if highliting:
            dwg_g.add(
                dwg_p.text(
                    "\U0001F006",
                    pos,
                    stroke=svgwrite.rgb(0, 0, 0, "%"),
                    stroke_width=highlight_stroke_width,
                    fill=highlight_fill,
                    opacity=highlight_opacity,
                )
            )


def _make_svg(
    sample_data: MahjongTable,
    target_idx: Optional[int] = None,
    show_name: bool = True,
    highlight_last_event: bool = True,
) -> Drawing:
    width = 800
    height = 800
    char_width = 32
    char_height = 44

    if target_idx is None:
        target_idx = sample_data.my_idx
    sample_data.players.sort(key=lambda x: (x.player_idx - target_idx) % 4)

    dwg = svgwrite.Drawing(
        "temp.svg",  # the file name is provided to saveas()
        (width, height),
        debug=True,
    )

    dwg._embed_font_data(
        "GL-MahjongTile",
        _read_asset("GL-MahjongTile.ttf"),
        "application/x-font-ttf",
    )

    player_g = dwg.g()

    players: List[Drawing] = [dwg.g(), dwg.g(), dwg.g(), dwg.g()]
    pai: List[Drawing] = [dwg.g(), dwg.g(), dwg.g(), dwg.g()]
    player_info: List[Drawing] = [dwg.g(), dwg.g(), dwg.g(), dwg.g()]
    winds: List[str] = ["", "", "", ""]
    scores: List[str] = ["", "", "", ""]
    is_riichi = [False, False, False, False]

    # Tuple[char, is_red, Tile]
    hands: List[List[Tuple[str, bool, Tile]]] = [[], [], [], []]
    open_tiles: List[
        List[Tuple[List[Tuple[str, bool, Tile]], Optional[RelativePlayerIdx], TileUnitType]]
    ] = [
        [],
        [],
        [],
        [],
    ]
    discards: List[List[Tuple[str, bool, Tile]]] = [[], [], [], []]

    for i in range(4):  # i: seat index sorted from the viewer's perspective
        players[i] = dwg.g()
        pai[i] = dwg.g(style="font-size:50px;font-family:GL-MahjongTile;")
        player_info[i] = dwg.g()

        winds[i] = get_wind_char(sample_data.players[i].wind, lang=1)
        scores[i] = sample_data.players[i].score
        is_riichi[i] = sample_data.players[i].is_declared_riichi

        for t_u in reversed(sample_data.players[i].tile_units):
            if t_u.tile_unit_type == TileUnitType.HAND:
                for tile in t_u.tiles:
                    hands[i].append(
                        (
                            "\U0001F02B" if not tile.is_open else get_tile_char(tile.id(), True),
                            tile.is_red(),
                            tile,
                        )
                    )

            if t_u.tile_unit_type == TileUnitType.DISCARD:
                for tile in t_u.tiles:
                    discards[i].append((get_tile_char(tile.id(), True), tile.is_red(), tile))
            if t_u.tile_unit_type in [
                TileUnitType.CHI,
                TileUnitType.PON,
                TileUnitType.CLOSED_KAN,
                TileUnitType.OPEN_KAN,
                TileUnitType.ADDED_KAN,
            ]:
                open_tiles[i].append(
                    (
                        [
                            (get_tile_char(tile.id(), True), tile.is_red(), tile)
                            for tile in t_u.tiles
                        ],
                        t_u.from_who,
                        t_u.tile_unit_type,
                    )
                )
    dwg.add(dwg.rect(insert=(0, 0), size=(width, height)))
    dwg.add(dwg.rect(insert=(1, 1), size=(width - 2, height - 2), fill="rgb(255,255,255)"))

    dwg.add(dwg.rect(insert=(278, 278), size=(244, 244)))
    dwg.add(dwg.rect(insert=(279, 279), size=(242, 242), fill="rgb(255,255,255)"))

    # board info
    round_str = (
        get_wind_char((sample_data.round - 1) // 4, lang=1)
        + str((sample_data.round - 1) % 4 + 1)
        + "局"
    )
    if sample_data.honba > 0:
        dwg.add(dwg.text(round_str, (340, 360), style="font-size:24px;font-family:serif;"))
        honba = str(sample_data.honba) + "本場"
        dwg.add(dwg.text(honba, (400, 360), style="font-size:24px;font-family:serif;"))
    else:
        dwg.add(dwg.text(round_str, (368, 360), style="font-size:26px;font-family:serif;"))

    # dora
    doras = [get_tile_char(tile, True) for tile in sample_data.doras]
    while len(doras) < 5:
        doras.append("\U0001F02B")
    dwg.add(
        dwg.text("".join(doras), (337, 400), style="font-size:40px;font-family:GL-MahjongTile;")
    )
    for i, dora in enumerate(sample_data.doras):
        if dora == sample_data.new_dora and highlight_last_event:
            dwg.add(
                dwg.text(
                    "\U0001F006",
                    (337 + i * 25.6, 400),
                    style="font-size:40px;font-family:GL-MahjongTile;",
                    stroke=svgwrite.rgb(0, 0, 0, "%"),
                    stroke_width=2.0,
                )
            )

    # deposit/honba sticks
    b64_1000_mini = base64.b64encode(_read_asset("1000_mini.svg"))
    thousand_mini_img = dwg.image("data:image/svg+xml;base64," + b64_1000_mini.decode("ascii"))
    thousand_mini_img.translate(335, 405)
    thousand_mini_img.scale(0.15)
    dwg.add(thousand_mini_img)
    dwg.add(
        dwg.text(
            f"×{sample_data.riichi}",
            (355, 430),
            style="font-size:22px;font-family:serif;",
        )
    )

    b64_hundred_mini = base64.b64encode(_read_asset("100_mini.svg"))
    hundred_mini_img = dwg.image("data:image/svg+xml;base64," + b64_hundred_mini.decode("ascii"))
    hundred_mini_img.translate(405, 405)
    hundred_mini_img.scale(0.15)
    dwg.add(hundred_mini_img)
    dwg.add(
        dwg.text(f"×{sample_data.honba}", (425, 430), style="font-size:22px;font-family:serif;")
    )

    # remaining wall
    wall_num = sample_data.wall_num
    dwg.add(
        dwg.text(
            "\U0001F02B",
            (370, 465),
            style="font-size:30px;font-family:GL-MahjongTile;",
        )
    )
    dwg.add(
        dwg.text(
            "×" + str(wall_num),
            (390, 463),
            style="font-size:22px;font-family:serif;",
        )
    )

    for i in range(4):
        # name
        if show_name:
            player_info[i].add(
                dwg.text(
                    sample_data.players[i].name,
                    (10, 790),
                    style="font-size:18px;font-family:serif;",
                )
            )

        # wind
        player_info[i].add(
            dwg.text(winds[i], (280, 515), style="font-size:30px;font-family:serif;")
        )

        # score
        scores[i] = scores[i].replace("(", " (")
        score_x = width / 2 - len(scores[i]) / 2 * 11
        player_info[i].add(
            dwg.text(scores[i], (score_x, 490), style="font-size:20px;font-family:serif;")
        )

        # riichi stick
        if is_riichi[i]:
            b64_thousand = base64.b64encode(_read_asset("1000.svg"))
            thousand_img = dwg.image("data:image/svg+xml;base64," + b64_thousand.decode("ascii"))
            thousand_img.translate(476, 485)
            thousand_img.scale(0.4)
            thousand_img.rotate(90)
            player_info[i].add(thousand_img)

        left_margin = 190
        # hand
        for j, hand in enumerate(hands[i]):
            hand_txt = hand[0]
            dwg_add(
                dwg,
                pai[i],
                (left_margin + j * char_width, 770),
                hand_txt,
                is_red=hand[1],
                highliting=hand[2].is_highlighting and highlight_last_event,
            )

        # discards
        riichi_idx = 100000
        for j, discard in enumerate(discards[i]):
            discard_txt = discard[0]
            if discard[2].with_riichi:  # riichi tile, drawn sideways
                riichi_idx = j
                dwg_add(
                    dwg,
                    pai[i],
                    (535 + (j // 6) * char_height, -307 - (j % 6) * char_width),
                    discard_txt,
                    rotate=True,
                    is_red=discard[1],
                    transparent=discard[2].is_transparent,  # claimed by someone
                    highliting=discard[2].is_highlighting and highlight_last_event,
                )

                if discard[2].is_tsumogiri:
                    dwg_add(
                        dwg,
                        pai[i],
                        (535 + (j // 6) * char_height, -307 - (j % 6) * char_width),
                        "\U0001F02B",
                        rotate=True,
                        transparent=discard[2].is_transparent,
                        highliting=discard[2].is_highlighting and highlight_last_event,
                    )

            elif (riichi_idx < j) and (j // 6 == riichi_idx // 6):
                # tiles after the sideways riichi tile in the same row are
                # shifted right to make room for it
                dwg_add(
                    dwg,
                    pai[i],
                    (
                        304 + char_height - char_width + (j % 6) * char_width,
                        570 + (j // 6) * char_height,
                    ),
                    discard_txt,
                    is_red=discard[1],
                    transparent=discard[2].is_transparent,
                    highliting=discard[2].is_highlighting and highlight_last_event,
                )

                if discard[2].is_tsumogiri:
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            304 + char_height - char_width + (j % 6) * char_width,
                            570 + (j // 6) * char_height,
                        ),
                        "\U0001F02B",
                        transparent=discard[2].is_transparent,
                        highliting=discard[2].is_highlighting and highlight_last_event,
                    )
            else:
                dwg_add(
                    dwg,
                    pai[i],
                    (304 + (j % 6) * char_width, 570 + (j // 6) * char_height),
                    discard_txt,
                    is_red=discard[1],
                    transparent=discard[2].is_transparent,
                    highliting=discard[2].is_highlighting and highlight_last_event,
                )

                if discard[2].is_tsumogiri:
                    dwg_add(
                        dwg,
                        pai[i],
                        (304 + (j % 6) * char_width, 570 + (j // 6) * char_height),
                        "\U0001F02B",
                        transparent=discard[2].is_transparent,
                        highliting=discard[2].is_highlighting and highlight_last_event,
                    )

        num_of_tehai = len(hands[i]) + len(open_tiles[i]) * 3
        left_x = char_width if num_of_tehai == 13 else 0

        for open_tile in open_tiles[i]:
            if open_tile[2] == TileUnitType.CHI:
                chi = open_tile[0]
                dwg_add(
                    dwg,
                    pai[i],
                    (
                        741.3,
                        -left_margin  # base position
                        - 3  # rotation offset
                        - (len(hands[i]) + 1) * char_width  # hand offset
                        - left_x,  # other melds offset
                    ),
                    chi[0][0],
                    rotate=True,
                    is_red=chi[0][1],
                    highliting=chi[0][2].is_highlighting and highlight_last_event,
                )
                dwg_add(
                    dwg,
                    pai[i],
                    (
                        left_margin + (len(hands[i]) + 1) * char_width + left_x + char_height,
                        770,
                    ),
                    chi[1][0],
                    is_red=chi[1][1],
                    highliting=chi[0][2].is_highlighting and highlight_last_event,
                )
                dwg_add(
                    dwg,
                    pai[i],
                    (
                        left_margin
                        + (len(hands[i]) + 1) * char_width
                        + left_x
                        + char_height
                        + char_width,
                        770,
                    ),
                    chi[2][0],
                    is_red=chi[2][1],
                    highliting=chi[0][2].is_highlighting and highlight_last_event,
                )

                left_x += char_width * 2 + char_height

            elif open_tile[2] == TileUnitType.PON:
                pon = open_tile[0]
                if open_tile[1] == RelativePlayerIdx.LEFT:
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            741.3,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x,
                        ),
                        pon[0][0],
                        rotate=True,
                        is_red=pon[0][1],
                        highliting=pon[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x + char_height,
                            770,
                        ),
                        pon[1][0],
                        is_red=pon[1][1],
                        highliting=pon[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin
                            + (len(hands[i]) + 1) * char_width
                            + left_x
                            + char_height
                            + char_width,
                            770,
                        ),
                        pon[2][0],
                        is_red=pon[2][1],
                        highliting=pon[0][2].is_highlighting and highlight_last_event,
                    )

                elif open_tile[1] == RelativePlayerIdx.CENTER:
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            741.3,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x
                            - char_width,  # first tile offset
                        ),
                        pon[0][0],
                        rotate=True,
                        is_red=pon[0][1],
                        highliting=pon[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x,
                            770,
                        ),
                        pon[1][0],
                        is_red=pon[1][1],
                        highliting=pon[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin
                            + (len(hands[i]) + 1) * char_width
                            + left_x
                            + char_height
                            + char_width,
                            770,
                        ),
                        pon[2][0],
                        is_red=pon[2][1],
                        highliting=pon[0][2].is_highlighting and highlight_last_event,
                    )

                elif open_tile[1] == RelativePlayerIdx.RIGHT:
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            741.3,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x
                            - char_width  # first tile offset
                            - char_width,  # second tile offset
                        ),
                        pon[0][0],
                        rotate=True,
                        is_red=pon[0][1],
                        highliting=pon[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x,
                            770,
                        ),
                        pon[1][0],
                        is_red=pon[1][1],
                        highliting=pon[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x + char_width,
                            770,
                        ),
                        pon[2][0],
                        is_red=pon[2][1],
                        highliting=pon[0][2].is_highlighting and highlight_last_event,
                    )
                left_x += char_width * 2 + char_height

            elif open_tile[2] == TileUnitType.CLOSED_KAN:
                closed_kan = open_tile[0]
                dwg_add(
                    dwg,
                    pai[i],
                    (
                        left_margin + (len(hands[i]) + 1) * char_width + left_x,
                        770,
                    ),
                    "\U0001F02B",
                    highliting=closed_kan[0][2].is_highlighting and highlight_last_event,
                )
                dwg_add(
                    dwg,
                    pai[i],
                    (
                        left_margin + (len(hands[i]) + 1) * char_width + left_x + char_width,
                        770,
                    ),
                    closed_kan[1][0],
                    is_red=closed_kan[1][1],
                    highliting=closed_kan[0][2].is_highlighting and highlight_last_event,
                )
                dwg_add(
                    dwg,
                    pai[i],
                    (
                        left_margin + (len(hands[i]) + 1) * char_width + left_x + char_width * 2,
                        770,
                    ),
                    closed_kan[2][0],
                    is_red=closed_kan[2][1],
                    highliting=closed_kan[0][2].is_highlighting and highlight_last_event,
                )
                dwg_add(
                    dwg,
                    pai[i],
                    (
                        left_margin + (len(hands[i]) + 1) * char_width + left_x + char_width * 3,
                        770,
                    ),
                    "\U0001F02B",
                    highliting=closed_kan[0][2].is_highlighting and highlight_last_event,
                )
                left_x += char_width * 4

            elif open_tile[2] == TileUnitType.OPEN_KAN:
                open_tile_kan = open_tile[0]
                if open_tile[1] == RelativePlayerIdx.LEFT:
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            741.3,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x,
                        ),
                        open_tile_kan[0][0],
                        rotate=True,
                        is_red=open_tile_kan[0][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x + char_height,
                            770,
                        ),
                        open_tile_kan[1][0],
                        is_red=open_tile_kan[1][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin
                            + (len(hands[i]) + 1) * char_width
                            + left_x
                            + char_height
                            + char_width,
                            770,
                        ),
                        open_tile_kan[2][0],
                        is_red=open_tile_kan[2][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin
                            + (len(hands[i]) + 1) * char_width
                            + left_x
                            + char_height
                            + char_width * 2,
                            770,
                        ),
                        open_tile_kan[3][0],
                        is_red=open_tile_kan[3][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )

                elif open_tile[1] == RelativePlayerIdx.CENTER:
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            741.3,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x
                            - char_width,
                        ),
                        txt=open_tile_kan[0][0],
                        rotate=True,
                        is_red=open_tile_kan[0][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x,
                            770,
                        ),
                        open_tile_kan[1][0],
                        is_red=open_tile_kan[1][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin
                            + (len(hands[i]) + 1) * char_width
                            + left_x
                            + char_height
                            + char_width,
                            770,
                        ),
                        open_tile_kan[2][0],
                        is_red=open_tile_kan[2][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin
                            + (len(hands[i]) + 1) * char_width
                            + left_x
                            + char_height
                            + char_width * 2,
                            770,
                        ),
                        open_tile_kan[3][0],
                        is_red=open_tile_kan[3][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )

                elif open_tile[1] == RelativePlayerIdx.RIGHT:
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            741.3,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x
                            - char_width * 3,
                        ),
                        txt=open_tile_kan[0][0],
                        rotate=True,
                        is_red=open_tile_kan[0][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x,
                            770,
                        ),
                        open_tile_kan[1][0],
                        is_red=open_tile_kan[1][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x + char_width,
                            770,
                        ),
                        open_tile_kan[2][0],
                        is_red=open_tile_kan[2][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin
                            + (len(hands[i]) + 1) * char_width
                            + left_x
                            + char_width * 2,
                            770,
                        ),
                        open_tile_kan[3][0],
                        is_red=open_tile_kan[3][1],
                        highliting=open_tile_kan[0][2].is_highlighting and highlight_last_event,
                    )
                left_x += char_width * 3 + char_height

            elif open_tile[2] == TileUnitType.ADDED_KAN:
                added_kan = open_tile[0]
                if open_tile[1] == RelativePlayerIdx.LEFT:
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            741.3,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x,
                        ),
                        txt=added_kan[0][0],
                        rotate=True,
                        is_red=added_kan[0][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            710,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x,
                        ),
                        added_kan[1][0],
                        rotate=True,
                        is_red=added_kan[1][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x + char_height,
                            770,
                        ),
                        added_kan[2][0],
                        is_red=added_kan[2][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin
                            + (len(hands[i]) + 1) * char_width
                            + left_x
                            + char_height
                            + char_width,
                            770,
                        ),
                        added_kan[3][0],
                        is_red=added_kan[3][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )

                elif open_tile[1] == RelativePlayerIdx.CENTER:
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            741.3,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x
                            - char_width,
                        ),
                        added_kan[1][0],
                        rotate=True,
                        is_red=added_kan[1][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            710,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x
                            - char_width,
                        ),
                        added_kan[2][0],
                        rotate=True,
                        is_red=added_kan[2][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x,
                            770,
                        ),
                        added_kan[0][0],
                        is_red=added_kan[0][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin
                            + (len(hands[i]) + 1) * char_width
                            + left_x
                            + char_height
                            + char_width,
                            770,
                        ),
                        added_kan[3][0],
                        is_red=added_kan[3][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )

                elif open_tile[1] == RelativePlayerIdx.RIGHT:
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            741.3,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x
                            - char_width * 2,
                        ),
                        added_kan[2][0],
                        rotate=True,
                        is_red=added_kan[2][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            710,
                            -left_margin  # base position
                            - 3  # rotation offset
                            - (len(hands[i]) + 1) * char_width  # hand offset
                            - left_x
                            - char_width * 2,
                        ),
                        added_kan[3][0],
                        rotate=True,
                        is_red=added_kan[3][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x,
                            770,
                        ),
                        added_kan[0][0],
                        is_red=added_kan[0][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )
                    dwg_add(
                        dwg,
                        pai[i],
                        (
                            left_margin + (len(hands[i]) + 1) * char_width + left_x + char_width,
                            770,
                        ),
                        added_kan[1][0],
                        is_red=added_kan[1][1],
                        highliting=added_kan[0][2].is_highlighting and highlight_last_event,
                    )
                left_x += char_width * 2 + char_height

        players[i].add(pai[i])
        players[i].add(player_info[i])
        players[i].rotate((360 - i * 90), (width / 2, height / 2))
        player_g.add(players[i])

    dwg.add(player_g)
    return dwg


def _to_table(
    data: Union[MahjongTable, List[Event]],
    index: Optional[int] = None,
    perspective: int = 0,
) -> MahjongTable:
    if isinstance(data, MahjongTable):
        return data
    return MahjongTable.from_mjai_events(data, index=index, perspective=perspective)


def to_svg(
    data: Union[MahjongTable, List[Event]],
    index: Optional[int] = None,
    target_idx: Optional[int] = None,
    show_name: bool = True,
    highlight_last_event: bool = True,
) -> str:
    """Render a board as an SVG string.

    ``data`` is either a decoded ``MahjongTable`` or a list of mjai events
    (in which case ``index`` selects the event to render, defaulting to the
    last one).
    """
    table = _to_table(data, index, target_idx if target_idx is not None else 0)
    dwg = _make_svg(
        table, target_idx, show_name=show_name, highlight_last_event=highlight_last_event
    )
    return dwg.tostring()


def save_svg(
    data: Union[MahjongTable, List[Event]],
    filename: str = "board.svg",
    index: Optional[int] = None,
    target_idx: Optional[int] = None,
    show_name: bool = True,
    highlight_last_event: bool = True,
) -> None:
    """Render a board and save it as an SVG file."""
    table = _to_table(data, index, target_idx if target_idx is not None else 0)
    dwg = _make_svg(
        table, target_idx, show_name=show_name, highlight_last_event=highlight_last_event
    )
    dwg.saveas(filename=filename)


def show_svg(
    data: Union[MahjongTable, List[Event]],
    index: Optional[int] = None,
    target_idx: Optional[int] = None,
    show_name: bool = True,
    highlight_last_event: bool = True,
) -> None:
    """Display the board inline in a Jupyter notebook."""
    import sys

    svg_str = to_svg(
        data,
        index=index,
        target_idx=target_idx,
        show_name=show_name,
        highlight_last_event=highlight_last_event,
    )

    if "ipykernel" in sys.modules:
        from IPython.display import display_svg

        display_svg(svg_str, raw=True)
    else:
        sys.stdout.write("This function only works in Jupyter Notebook.\n")
