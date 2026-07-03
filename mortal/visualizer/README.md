# Board visualizer for mjai logs

Renders the full four-player board state (hands, discard rivers, melds,
dora indicators, scores, riichi sticks, ...) of any moment of an mjai game
log, either as a self-contained SVG image or as text in the terminal.

This component is ported from [mjx](https://github.com/mjx-project/mjx)
(MIT License) and adapted to consume the mjai event format used across
Mortal instead of mjx's protobuf messages. The bundled assets come from
mjx as well: the `GL-MahjongTile.ttf` font (whose glyphs for the Unicode
mahjong tile block are the tile images embedded into the SVG output) and
the score stick images (`1000.svg`, `1000_mini.svg`, `100_mini.svg`).

## Usage

The CLI accepts any mjai log with one JSON event per line — for example a
(possibly gzip-compressed) log written by `libriichi::arena`, or a log in
the format rendered by `log-viewer`. Run inside the `mortal` directory:

```sh
# render the final board of the game to game.svg
$ python -m visualizer game.json.gz

# board right after event #42, seen from seat 2, as SVG
$ python -m visualizer game.json.gz --index 42 --perspective 2 --out board.svg

# list all events with their indices to pick from
$ python -m visualizer game.json.gz --list

# plain text / unicode / rich output in the terminal
$ python -m visualizer game.json.gz --mode text --index 42
$ python -m visualizer game.json.gz --mode text --uni --lang ja
$ python -m visualizer game.json.gz --mode rich

# dump one SVG per event (a flip book of the whole game)
$ python -m visualizer game.json.gz --all-dir out/
```

Programmatic use:

```python
from visualizer import MahjongTable, load_mjai_log, save_svg, to_svg

events = load_mjai_log("game.json.gz")

# events can also be passed straight to the svg helpers
save_svg(events, "board.svg", index=42)
svg_string = to_svg(events)

# or decode explicitly to inspect the board state
table = MahjongTable.from_mjai_events(events, index=42, perspective=2)
```

In a Jupyter notebook, `visualizer.show_svg(events, index=42)` displays
the board inline.

Try it on the example log embedded in the log viewer:

```sh
$ python - <<'EOF'
src = open('../log-viewer/index.example.html').read()
body = src.split('allActions = `', 1)[1].split('`', 1)[0]
open('/tmp/example.json', 'w').write(body)
EOF
$ python -m visualizer /tmp/example.json --index 30 --mode text
```

## Notes

- Hidden tiles (mjai `"?"`, e.g. opponents' hands in a log recorded from
  one seat's point of view) are drawn face down.
- Red fives are drawn with a red number in the SVG output and mapped to
  the `5mr`/`5pr`/`5sr` mjai notation when decoding.
- SVG output needs the `svgwrite` package; `--mode rich` needs `rich`.
  Plain `--mode text` works without any extra dependency.
