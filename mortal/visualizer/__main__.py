"""Command line interface for the mjai board visualizer.

Run from the ``mortal`` directory::

    python -m visualizer <log> [options]

where ``<log>`` is an mjai log (one JSON event per line, optionally
gzip-compressed, e.g. the ``*.json.gz`` files written by the arena) or
``-`` for stdin.
"""

import argparse
import os
import sys

from .visualizer import GameBoardVisualizer, GameVisualConfig, MahjongTable, load_mjai_log


def _list_events(events) -> None:
    for i, eve in enumerate(events):
        etype = eve.get("type", "?")
        parts = [f"{i:4d}  {etype}"]
        if "actor" in eve:
            parts.append(f"actor={eve['actor']}")
        if "target" in eve and eve.get("target") != eve.get("actor"):
            parts.append(f"target={eve['target']}")
        if "pai" in eve:
            parts.append(f"pai={eve['pai']}")
        if "consumed" in eve:
            parts.append(f"consumed={','.join(eve['consumed'])}")
        if "dora_marker" in eve:
            parts.append(f"dora_marker={eve['dora_marker']}")
        if etype == "start_kyoku":
            parts.append(
                f"bakaze={eve['bakaze']} kyoku={eve['kyoku']} honba={eve['honba']}"
            )
        if eve.get("tsumogiri"):
            parts.append("tsumogiri")
        print("  ".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m visualizer",
        description="Render a board state from an mjai game log.",
    )
    parser.add_argument("log", help="mjai log file (.json/.json.gz) or - for stdin")
    parser.add_argument(
        "--out",
        "-o",
        default=None,
        help="output SVG path (default: <log>.svg); ignored for text/rich modes",
    )
    parser.add_argument(
        "--mode",
        choices=["svg", "text", "rich"],
        default="svg",
        help="output mode (default: svg)",
    )
    parser.add_argument(
        "--index",
        "-i",
        type=int,
        default=None,
        help="event index to render the board at (default: last event; "
        "negative values count from the end)",
    )
    parser.add_argument(
        "--perspective",
        "-p",
        type=int,
        choices=range(4),
        default=0,
        help="seat shown at the bottom (default: 0)",
    )
    parser.add_argument(
        "--all-dir",
        default=None,
        help="render every event of the log as SVG files into this directory",
    )
    parser.add_argument(
        "--review",
        nargs="?",
        type=int,
        const=-1,
        default=None,
        metavar="SEAT",
        help="build an interactive whole-game move-evaluation HTML page from "
        "the meta.q_values the engine wrote into the log; SEAT picks the "
        "seat to review (default: the seat with the most evaluations)",
    )
    parser.add_argument("--list", action="store_true", help="list events with indices and exit")
    parser.add_argument("--uni", action="store_true", help="use Unicode tile glyphs in text mode")
    parser.add_argument(
        "--lang",
        choices=["en", "ja"],
        default="en",
        help="label language (default: en)",
    )
    parser.add_argument("--no-name", action="store_true", help="hide player names")
    parser.add_argument(
        "--no-highlight",
        action="store_true",
        help="do not highlight the latest action",
    )
    args = parser.parse_args()

    events = load_mjai_log(args.log)
    if not events:
        sys.exit("error: the log contains no events")

    if args.list:
        _list_events(events)
        return

    lang = 0 if args.lang == "en" else 1
    show_name = not args.no_name

    if args.review is not None:
        from .review import save_review_html

        actor = None if args.review == -1 else args.review
        out = args.out
        if out is None:
            base = os.path.basename(args.log) if args.log != "-" else "review"
            for suffix in (".gz", ".json", ".jsonl"):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
            out = base + ".review.html"
        try:
            save_review_html(events, out, actor=actor)
        except ValueError as e:
            sys.exit(f"error: {e}")
        print(f"wrote {out}")
        return

    if args.all_dir is not None:
        from .svg import save_svg

        os.makedirs(args.all_dir, exist_ok=True)
        digits = len(str(len(events) - 1))
        count = 0
        for i in range(len(events)):
            try:
                table = MahjongTable.from_mjai_events(
                    events, index=i, perspective=args.perspective
                )
            except ValueError:
                continue  # events before the first start_kyoku
            out = os.path.join(args.all_dir, f"board_{i:0{digits}d}.svg")
            save_svg(
                table,
                out,
                target_idx=args.perspective,
                show_name=show_name,
                highlight_last_event=not args.no_highlight,
            )
            count += 1
        print(f"wrote {count} SVG files to {args.all_dir}")
        return

    table = MahjongTable.from_mjai_events(
        events, index=args.index, perspective=args.perspective
    )

    if args.mode == "svg":
        from .svg import save_svg

        out = args.out
        if out is None:
            base = os.path.basename(args.log) if args.log != "-" else "board"
            for suffix in (".gz", ".json", ".jsonl"):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
            out = base + ".svg"
        save_svg(
            table,
            out,
            target_idx=args.perspective,
            show_name=show_name,
            highlight_last_event=not args.no_highlight,
        )
        print(f"wrote {out}")
    else:
        config = GameVisualConfig(
            uni=args.uni,
            rich=args.mode == "rich",
            lang=lang,
            show_name=show_name,
        )
        GameBoardVisualizer(config).print(table)


if __name__ == "__main__":
    main()
