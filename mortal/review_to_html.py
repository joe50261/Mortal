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


# ---------------------------------------------------------------------------
# Optional analysis overlay: render the recomputed q_values as a panel showing,
# for the current action, every candidate (tile / call) with its Q value and
# softmax probability, marking the model's best (star) and the actual move.
# ---------------------------------------------------------------------------
ANALYSIS_CSS = '''
<style>
/* Per-tile analysis overlay drawn on top of the board. Positioned in document
   space (absolute + scroll offset) so labels track tiles when scrolling. */
#mortal-overlay{position:absolute;left:0;top:0;pointer-events:none;z-index:2147483647}
#mortal-overlay .tl{position:absolute;transform:translateX(-50%);white-space:nowrap;
  font:700 11px/1.05 system-ui,sans-serif;padding:1px 3px;border-radius:3px;
  background:rgba(18,19,24,.86);color:#fff;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.5)}
#mortal-overlay .tl .p{display:block;font-weight:400;font-size:9px;color:#bcd}
#mortal-overlay .tl.best{background:#2f9e54}
#mortal-overlay .tl.cho{outline:2px solid #5aa0ff;outline-offset:1px}
#mortal-legend{position:fixed;left:8px;top:8px;z-index:2147483647;pointer-events:none;
  font:12px/1.5 system-ui,sans-serif;background:rgba(18,19,24,.82);color:#eee;
  padding:6px 9px;border-radius:6px;max-width:60vw}
#mortal-legend b{color:#fff}
</style>
'''

ANALYSIS_JS = '''
<script>
(function(){
  function masked(mask){var r=[];for(var i=0;i<46;i++){if(Math.floor(mask/Math.pow(2,i))%2===1)r.push(i);}return r;}
  function softmax(qs){var m=Math.max.apply(null,qs);var e=qs.map(function(q){return Math.exp(q-m);});
    var s=e.reduce(function(a,b){return a+b;},0);return e.map(function(x){return x/s;});}
  function tileToIdx(t){
    var m=/^([1-9])([mps])(r)?$/.exec(t);
    if(m){if(m[3])return {m:34,p:35,s:36}[m[2]];return {m:0,p:9,s:18}[m[2]]+(+m[1])-1;}
    var h=['E','S','W','N','P','F','C'].indexOf(t);return h>=0?27+h:-1;
  }
  function overlay(){var el=document.getElementById('mortal-overlay');
    if(!el){el=document.createElement('div');el.id='mortal-overlay';document.body.appendChild(el);}return el;}
  function legend(t){var el=document.getElementById('mortal-legend');
    if(!el){el=document.createElement('div');el.id='mortal-legend';document.body.appendChild(el);}
    el.style.display=t?'block':'none';el.innerHTML=t||'';}
  // For a tsumo frame, the decision is the next action by the same player.
  function decisionFor(a){
    if(!a||a.type!=='tsumo')return null;
    try{var acts=kyokus[currentKyokuId].actions,s=a.actor;
      for(var i=currentActionId+1;i<acts.length;i++){if(acts[i].actor===s)
        return (acts[i].meta&&acts[i].meta.q_values)?acts[i]:null;}
    }catch(e){}
    return null;
  }
  function chosenPai(dec,s){
    if(dec.pai)return dec.pai;
    try{var acts=kyokus[currentKyokuId].actions,z=acts.indexOf(dec);
      for(var i=z+1;i<acts.length;i++){if(acts[i].actor===s){
        if(acts[i].type==='dahai')return acts[i].pai; if(acts[i].type!=='reach')break;}}
    }catch(e){}
    return null;
  }
  function draw(a){
    var ov=overlay();ov.innerHTML='';
    var dec=decisionFor(a);
    if(!dec){legend('');return;}
    var s=a.actor,m=dec.meta,idx=masked(m.mask_bits),qs=m.q_values,pis=softmax(qs);
    var qByIdx={},pByIdx={},bestDi=-1,bestDq=-Infinity;
    for(var k=0;k<idx.length;k++){qByIdx[idx[k]]=qs[k];pByIdx[idx[k]]=pis[k];
      if(idx[k]<=36&&qs[k]>bestDq){bestDq=qs[k];bestDi=idx[k];}}
    var chosen=chosenPai(dec,s);
    var tiles=(a.board&&a.board.players[s])?a.board.players[s].tehais:null;
    if(!tiles){legend('');return;}
    var slot=((s-currentViewpoint)%4+4)%4;
    var root=document.querySelector('.player-'+slot+' .tehai-container');
    if(!root){legend('');return;}
    var hand=[].slice.call(root.querySelectorAll('img.pai:not(.tsumo-pai)'));
    var tsumo=root.querySelector('img.tsumo-pai');
    var pairs=[],i;
    for(i=0;i<hand.length;i++)pairs.push([hand[i],tiles[i]]);
    if(tsumo&&tsumo.offsetParent!==null)pairs.push([tsumo,tiles[tiles.length-1]]);
    var sx=window.pageXOffset,sy=window.pageYOffset;
    pairs.forEach(function(pr){
      var img=pr[0],t=pr[1];if(!img||!t)return;
      var ti=tileToIdx(t);if(!(ti in qByIdx))return;
      var r=img.getBoundingClientRect();if(!r.width)return;
      var d=document.createElement('div');
      d.className='tl'+(ti===bestDi?' best':'')+(t===chosen?' cho':'');
      d.style.left=(r.left+r.width/2+sx)+'px';d.style.top=(r.bottom+3+sy)+'px';
      d.innerHTML=(qByIdx[ti]>=0?'+':'')+qByIdx[ti].toFixed(2)+'<span class="p">'+(pByIdx[ti]*100).toFixed(0)+'%</span>';
      ov.appendChild(d);
    });
    legend('玩家 '+s+'（'+(dec.type==='reach'?'立直':'出牌')+'）｜<b>綠</b>=模型最佳　<b>藍框</b>=實際打出　數字=Q值/機率'+
      (m.shanten!=null&&m.shanten>=0?'　｜向聽 '+m.shanten:''));
  }
  // Scale the board to fit the viewport height (not width) so the whole board
  // and the per-tile labels are visible without vertical scrolling.
  function fit(){var w=document.getElementById('mortal-scale-wrap');if(!w)return;
    // 550 = board size; reserve ~50px for the per-tile label row below the hand
    var s=(window.innerHeight-50)/550;if(s>0.1)w.style.transform='scale('+s+')';}
  function install(){
    if(typeof renderAction!=='function'||typeof jQuery==='undefined'){return setTimeout(install,30);}
    var orig=renderAction;
    renderAction=function(a){
      // show the deciding player's hand at the bottom (unrotated) for readability
      try{if(a&&a.type==='tsumo'){var dd=decisionFor(a);if(dd&&currentViewpoint!==a.actor)currentViewpoint=a.actor;}}catch(e){}
      orig(a);
      try{draw(a);}catch(e){}
    };
    // refit and reposition labels when the window resizes
    window.addEventListener('resize',function(){fit();try{renderCurrentAction();}catch(e){}});
    // The viewer binds "mousewheel" via jQuery (passive in Chrome, so
    // preventDefault is ignored -> [Intervention] + page scroll). Use a
    // non-passive "wheel" listener for quiet, working wheel navigation.
    try{jQuery(window).off('mousewheel');
      window.addEventListener('wheel',function(e){if(typeof goNext!=='function')return;
        if(e.deltaY>0)goNext();else if(e.deltaY<0)goBack();e.preventDefault();},{passive:false});
    }catch(e){}
    // fit to height, then jump to the first discard decision so analysis is visible on open
    jQuery(function(){try{
      fit();
      var acts=kyokus[currentKyokuId].actions,j=-1,i,k;
      for(i=0;i<acts.length&&j<0;i++){if(acts[i].type==='tsumo'){var s=acts[i].actor;
        for(k=i+1;k<acts.length;k++){if(acts[k].actor===s){if(acts[k].meta&&acts[k].meta.q_values)j=i;break;}}}}
      if(j>=0){currentActionId=j;var lbl=document.getElementById('action-id-label');if(lbl)lbl.value=j;}
      renderCurrentAction();
    }catch(e){}});
  }
  install();
})();
</script>
'''


def inject_analysis(html):
    html = html.replace('</head>', ANALYSIS_CSS + '</head>', 1)
    html = html.replace('</body>', ANALYSIS_JS + '</body>', 1)
    return html


def enable_viewport_overlay(html):
    '''Move the viewer's `transform: scale()` off <body> onto an inner wrapper.

    The viewer scales <body>, which makes a position:fixed overlay use the
    transformed <body> as its containing block (scaled / mis-positioned). By
    wrapping the body content and transforming the wrapper instead, <body> has
    no transform, so the analysis panel can be a normal fixed child of <body>
    pinned to the viewport in every browser.
    '''
    override = ('<style>html,body{margin:0}body{transform:none !important}'
               '#mortal-scale-wrap{transform:scale(2.3);transform-origin:top left}</style>')
    html = html.replace('</head>', override + '</head>', 1)
    html = re.sub(r'(<body[^>]*>)', r'\1<div id="mortal-scale-wrap">', html, count=1)
    html = html.replace('</body>', '</div></body>', 1)
    return html


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
        "  var BLANK = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';\n"
        '  var _orig = paiToImageUrl;\n'
        '  // Never return a file:// path: Chrome/Safari block file->file subresource\n'
        '  // loads on file:// pages ("unique security origins"). Fall back to a\n'
        '  // transparent pixel so the page makes zero external requests.\n'
        '  paiToImageUrl = function(pai, pose){ var u = _orig(pai, pose); return IMG_MAP[u] || BLANK; };\n'
        '})();\n</script>\n'
    )
    return html.replace('</head>', patch + '</head>', 1)


def render_html(events, template_path, output_path, standalone=False, analysis=False):
    '''Inject the annotated log into a copy of the viewer template.'''
    with open(template_path, encoding='utf-8') as f:
        template = f.read()

    body = '\n'.join(json.dumps(e, ensure_ascii=False, separators=(',', ':')) for e in events)

    # Replace the content of the `allActions = ` ... `.trim().split(...)` literal.
    pattern = re.compile(r'(allActions\s*=\s*`)(.*?)(`\.trim\(\))', re.S)
    if not pattern.search(template):
        raise RuntimeError('could not locate the allActions template literal')
    html = pattern.sub(lambda m: m.group(1) + '\n' + body + '\n' + m.group(3), template, count=1)

    if analysis:
        # wrap the (clean) template body before injecting scripts, so the body
        # tag we match is the real one, not a `<body>` inside injected code.
        html = enable_viewport_overlay(html)
        html = inject_analysis(html)
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
    parser.add_argument('--analysis', action='store_true',
                        help='overlay a panel showing recomputed q_values per candidate action')
    parser.add_argument('--quiet', action='store_true', help='suppress progress output')
    args = parser.parse_args()

    device = torch.device(args.device)
    engine, tag = load_engine(args.model, device, quiet=args.quiet)
    events = read_mjai_log(args.input)

    start = time.perf_counter()
    events = recalculate(engine, events)
    elapsed = time.perf_counter() - start

    render_html(events, args.template, args.output, standalone=args.standalone, analysis=args.analysis)
    if not args.quiet:
        annotated = sum(1 for e in events if 'meta' in e)
        print(f'recalculated {annotated} decisions over {len(events)} events '
              f'in {elapsed:.1f}s -> {args.output}', file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
