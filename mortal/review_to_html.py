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
/* Appended to <html> (not <body>) so the page's `transform: scale()` on
   <body> does not scale or reposition this fixed overlay. */
#analysis{position:fixed;top:8px;right:8px;width:260px;max-height:96vh;overflow:auto;
  background:#1e1f24;color:#e8e8ea;font:12px/1.4 system-ui,sans-serif;border-radius:8px;
  box-shadow:0 2px 12px rgba(0,0,0,.45);padding:10px;z-index:2147483647}
#analysis h3{margin:0 0 2px;font-size:13px;font-weight:600}
#analysis .an-sub{color:#9aa0aa;font-size:11px;margin-bottom:8px}
#analysis .an-row{position:relative;display:flex;align-items:center;gap:6px;padding:3px 5px;
  border-radius:4px;margin-bottom:2px;background:#26272d;overflow:hidden}
#analysis .an-bar{position:absolute;left:0;top:0;bottom:0;background:rgba(90,150,220,.22);z-index:0}
#analysis .an-row>*{position:relative;z-index:1}
#analysis .an-row.best .an-bar{background:rgba(80,200,130,.38)}
#analysis .an-row.chosen{outline:2px solid #4a90d9;outline-offset:-2px}
#analysis .an-mark{width:14px;text-align:center;font-size:12px}
#analysis .an-pai{height:24px;width:auto;display:block}
#analysis .an-act{display:inline-block;min-width:22px;height:22px;line-height:22px;text-align:center;
  background:#3a3c44;border-radius:3px;padding:0 7px;font-weight:600}
#analysis .an-q{margin-left:auto;font-variant-numeric:tabular-nums;color:#dfe3ea}
#analysis .an-pi{font-variant-numeric:tabular-nums;color:#9aa0aa;min-width:38px;text-align:right}
#analysis .an-empty{color:#9aa0aa;padding:6px 2px}
#analysis .an-legend{margin-top:8px;color:#9aa0aa;font-size:11px}
</style>
'''

ANALYSIS_JS = '''
<script>
(function(){
  var ACT={37:'立直',38:'吃',39:'吃',40:'吃',41:'碰',42:'槓',43:'和了',44:'流局',45:'pass'};
  function label(i){
    if(i<=8)return (i+1)+'m';
    if(i<=17)return (i-8)+'p';
    if(i<=26)return (i-17)+'s';
    if(i<=33)return ['E','S','W','N','P','F','C'][i-27];
    if(i==34)return '5mr'; if(i==35)return '5pr'; if(i==36)return '5sr';
    return ACT[i]||('#'+i);
  }
  function masked(mask){var r=[];for(var i=0;i<46;i++){if(Math.floor(mask/Math.pow(2,i))%2===1)r.push(i);}return r;}
  function softmax(qs){var m=Math.max.apply(null,qs);var e=qs.map(function(q){return Math.exp(q-m);});
    var s=e.reduce(function(a,b){return a+b;},0);return e.map(function(x){return x/s;});}
  function chosen(idx,a){var t=a.type;
    if(idx<=36)return !!(a.pai&&label(idx)===a.pai);
    if(idx==37)return t==='reach';
    if(idx>=38&&idx<=40)return t==='chi';
    if(idx==41)return t==='pon';
    if(idx==42)return t==='ankan'||t==='kakan'||t==='daiminkan'||t==='kan';
    if(idx==43)return t==='hora';
    if(idx==44)return t==='ryukyoku';
    return false;}
  function panel(){var el=document.getElementById('analysis');
    if(!el){el=document.createElement('div');el.id='analysis';
      // attach to <html>, outside the scaled <body>, so position:fixed is
      // relative to the viewport and the panel is not scaled.
      document.documentElement.appendChild(el);}return el;}
  function curDecision(a){
    if(a&&a.meta&&a.meta.q_values)return a;
    try{var acts=kyokus[currentKyokuId].actions;
      for(var i=currentActionId;i>=0;i--){if(acts[i].meta&&acts[i].meta.q_values)return acts[i];}}catch(e){}
    return null;}
  function render(a){
    var el=panel();var d=curDecision(a);
    if(!d){el.innerHTML='<h3>Mortal 分析</h3><div class="an-empty">按 Next ▶ 到出牌或鳴牌的手即可看到分析</div>';return;}
    var m=d.meta;
    var idx=masked(m.mask_bits),qs=m.q_values,pis=softmax(qs);
    var ord=qs.map(function(q,k){return k;}).sort(function(x,y){return qs[y]-qs[x];});
    var best=ord[0];
    var sub='actor '+d.actor+' ・ '+d.type;
    if(m.shanten!=null&&m.shanten>=0)sub+=' ・ 向聽 '+m.shanten;
    if(m.at_furiten)sub+=' ・ 振聴';
    var h='<h3>Mortal 分析</h3><div class="an-sub">'+sub+'</div>';
    ord.forEach(function(k){
      var i=idx[k],lab=label(i),q=qs[k],pi=pis[k];
      var isBest=(k===best),isCho=chosen(i,d);
      var cell=(i<=36)?'<img class="an-pai" src="'+paiToImageUrl(lab)+'">':'<span class="an-act">'+lab+'</span>';
      var mark=(isBest?'★':'')+(isCho?'◉':'');
      h+='<div class="an-row'+(isBest?' best':'')+(isCho?' chosen':'')+'">'+
         '<span class="an-bar" style="width:'+(pi*100).toFixed(1)+'%"></span>'+
         '<span class="an-mark">'+mark+'</span>'+cell+
         '<span class="an-q">'+(q>=0?'+':'')+q.toFixed(2)+'</span>'+
         '<span class="an-pi">'+(pi*100).toFixed(1)+'%</span></div>';
    });
    h+='<div class="an-legend">★ 模型最佳・◉ 實際選擇・Q=價值・%=softmax 機率</div>';
    el.innerHTML=h;
  }
  function install(){
    if(typeof renderAction!=='function'||typeof jQuery==='undefined'){return setTimeout(install,30);}
    var orig=renderAction;
    renderAction=function(a){orig(a);try{render(a);}catch(e){}};
    jQuery(function(){try{
      var acts=kyokus[currentKyokuId].actions;
      var i=acts.findIndex(function(x){return x.meta&&x.meta.q_values;});
      if(i>=0){currentActionId=i;var lbl=document.getElementById('action-id-label');if(lbl)lbl.value=i;renderCurrentAction();}
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
