import re
s=open('src/index.src.html').read()
names=['hexagons','settings','player-pause','player-play','player-stop','camera-off','alert-triangle','check','info-circle','x','photo','power','maximize','heart-bolt','library-photo']
syms=[]
for n in names:
    svg=open(f'/home/claude/npmtmp/node_modules/@tabler/icons/icons/outline/{n}.svg').read()
    body=''.join(p for p in re.findall(r'<(?:path|circle|rect|line|polyline)[^>]*/>',svg) if 'stroke="none"' not in p)
    syms.append(f'<symbol id="t-{n}" viewBox="0 0 24 24">{body}</symbol>')
sprite='<svg width="0" height="0" style="position:absolute" aria-hidden="true">'+''.join(syms)+'</svg>'
open('static/index.html','w').write(s.replace('<!--ICONS-->',sprite))
print('ok',len(syms))
