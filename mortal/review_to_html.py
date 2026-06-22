#!/usr/bin/env python
'''Recalculate (review) a single mjai game log with a Mortal model and render a
standalone visualization web page.

This re-evaluates every decision point of an mjai game log with a trained Mortal
model (recomputing the Q values / meta for all four seats) and bakes the
annotated log into a copy of the log viewer (``log-viewer/index.example.html``),
producing a self-contained HTML file that replays the game on a board.

It only needs the ``libriichi`` extension module, ``torch`` and the model files;
it deliberately avoids importing ``config``/``prelude`` so that it can run
without a full training config or tensorboard.

Example:
    python review_to_html.py \
        --model /path/to/mortal.pth \
        --input game.mjai.json \
        --output ../log-viewer/index.html
'''

import os
import sys
import re
import json
import time
import base64
import argparse
from datetime import datetime, timezone

import torch

from model import Brain, DQN
from engine import MortalEngine
from libriichi.mjai import Bot

# Actual log event types that represent a seat's own decision and therefore can
# carry the recomputed `meta` (q_values etc.).
ACTION_TYPES = frozenset({
    'dahai', 'reach', 'pon', 'chi',
    'ankan', 'kakan', 'daiminkan', 'hora',
    'ryukyoku', 'nukidora',
})


def load_engine(model_path, device, *, quiet=False):
    '''Load a Mortal checkpoint and build a MortalEngine in review mode.'''
    state = torch.load(model_path, weights_only=True, map_location=device)
    cfg = state['config']
    version = cfg['control'].get('version', 1)
    num_blocks = cfg['resnet']['num_blocks']
    conv_channels = cfg['resnet']['conv_channels']
    if 'tag' in state:
        tag = state['tag']
    else:
        ts = datetime.fromtimestamp(state['timestamp'], tz=timezone.utc).strftime('%y%m%d%H')
        tag = f'mortal{version}-b{num_blocks}c{conv_channels}-t{ts}'

    mortal = Brain(version=version, num_blocks=num_blocks, conv_channels=conv_channels).eval()
    dqn = DQN(version=version).eval()
    mortal.load_state_dict(state['mortal'])
    dqn.load_state_dict(state['current_dqn'])

    engine = MortalEngine(
        mortal,
        dqn,
        version=version,
        is_oracle=False,
        device=device,
        enable_amp=False,
        enable_quick_eval=False,  # review mode: compute full q_values
        enable_rule_based_agari_guard=True,
        name='mortal',
    )
    if not quiet:
        print(f'loaded model: tag={tag} version={version} '
              f'blocks={num_blocks} channels={conv_channels}', file=sys.stderr)
    return engine, tag


def read_mjai_log(path):
    '''Read an mjai log file, returning a list of event dicts with meta stripped.'''
    events = []
    with open(path, encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get('type') is None:
                # skip trailing review extra-data lines (model_tag / phi_matrix)
                continue
            obj.pop('meta', None)
            events.append(obj)
    return events


def review_seat(engine, player_id, lines):
    '''Run a single bot over the whole log; return its reaction per input line.'''
    bot = Bot(engine, player_id)
    reactions = []
    for line in lines:
        out = bot.react(line)
        reactions.append(json.loads(out) if out else None)
    return reactions


def recalculate(engine, events):
    '''Recompute meta for every seat and attach it to the actual actions.

    The bot for seat ``s`` reacts to the line that triggers its decision (its own
    tsumo, or another player's discard). That reaction's meta belongs to the
    actual action seat ``s`` takes on the very next line, when that line is one
    of seat ``s``'s own decision events.
    '''
    lines = [json.dumps(e, ensure_ascii=False) for e in events]
    n = len(events)
    for s in range(4):
        reactions = review_seat(engine, s, lines)
        for i, reaction in enumerate(reactions):
            if not reaction:
                continue
            meta = reaction.get('meta')
            if meta is None:
                continue
            j = i + 1
            if j < n:
                nxt = events[j]
                if nxt.get('actor') == s and nxt.get('type') in ACTION_TYPES \
                        and 'meta' not in nxt:
                    nxt['meta'] = meta
    return events


_IMG_MIME = {'.gif': 'image/gif', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}


def inline_assets(html, base_dir):
    '''Inline CSS, JS and tile images so the page is a single self-contained file.'''
    # Inline the stylesheet.
    def repl_css(m):
        css = open(os.path.join(base_dir, m.group(1)), encoding='utf-8').read()
        return f'<style>\n{css}\n</style>'
    html = re.sub(r'<link[^>]*href="([^"]+\.css)"[^>]*>', repl_css, html)

    # Inline external scripts.
    def repl_js(m):
        js = open(os.path.join(base_dir, m.group(1)), encoding='utf-8').read()
        return f'<script>\n{js}\n</script>'
    html = re.sub(r'<script\s+src="([^"]+\.js)"\s*></script>', repl_js, html)

    # Inline tile images as a base64 map and route paiToImageUrl through it.
    img_dir = os.path.join(base_dir, 'files', 'images')
    img_map = {}
    if os.path.isdir(img_dir):
        for name in sorted(os.listdir(img_dir)):
            ext = os.path.splitext(name)[1].lower()
            mime = _IMG_MIME.get(ext)
            if not mime:
                continue
            with open(os.path.join(img_dir, name), 'rb') as f:
                data = base64.b64encode(f.read()).decode('ascii')
            img_map[f'files/images/{name}'] = f'data:{mime};base64,{data}'
    patch = (
        '<script>\n(function(){\n'
        f'  var IMG_MAP = {json.dumps(img_map)};\n'
        '  var _orig = paiToImageUrl;\n'
        '  paiToImageUrl = function(pai, pose){ var u = _orig(pai, pose); return IMG_MAP[u] || u; };\n'
        '})();\n</script>\n'
    )
    return html.replace('</head>', patch + '</head>', 1)


def render_html(events, template_path, output_path, standalone=False):
    '''Inject the annotated log into a copy of the viewer template.'''
    with open(template_path, encoding='utf-8') as f:
        template = f.read()

    body = '\n'.join(json.dumps(e, ensure_ascii=False, separators=(',', ':')) for e in events)

    # Replace the content of the `allActions = ` ... `.trim().split(...)` literal.
    pattern = re.compile(r'(allActions\s*=\s*`)(.*?)(`\.trim\(\))', re.S)
    if not pattern.search(template):
        raise RuntimeError('could not locate the allActions template literal')
    html = pattern.sub(lambda m: m.group(1) + '\n' + body + '\n' + m.group(3), template, count=1)

    if standalone:
        html = inline_assets(html, os.path.dirname(os.path.abspath(template_path)))

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_template = os.path.normpath(os.path.join(here, '..', 'log-viewer', 'index.example.html'))

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', required=True, help='path to the Mortal model checkpoint (.pth)')
    parser.add_argument('--input', required=True, help='path to the mjai game log to recalculate')
    parser.add_argument('--output', required=True, help='path of the HTML file to write')
    parser.add_argument('--template', default=default_template, help='HTML template (default: log-viewer/index.example.html)')
    parser.add_argument('--device', default='cpu', help='torch device (default: cpu)')
    parser.add_argument('--standalone', action='store_true',
                        help='inline CSS/JS/images into a single self-contained HTML file')
    parser.add_argument('--quiet', action='store_true', help='suppress progress output')
    args = parser.parse_args()

    device = torch.device(args.device)
    engine, tag = load_engine(args.model, device, quiet=args.quiet)
    events = read_mjai_log(args.input)

    start = time.perf_counter()
    events = recalculate(engine, events)
    elapsed = time.perf_counter() - start

    render_html(events, args.template, args.output, standalone=args.standalone)
    if not args.quiet:
        annotated = sum(1 for e in events if 'meta' in e)
        print(f'recalculated {annotated} decisions over {len(events)} events '
              f'in {elapsed:.1f}s -> {args.output}', file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
