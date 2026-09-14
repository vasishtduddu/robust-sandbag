import re,sys,glob,os,csv
def parse(path):
    out=[]
    for line in open(path,errors='replace').read().replace('\r','\n').split('\n'):
        m=re.search(r'predicted-letter histogram: (.+)$',line)
        if m:
            try: d={k.strip():int(v) for k,v in (kv.split('=') for kv in m.group(1).split(','))}
            except ValueError: continue
            out.append(d)
        elif 'STEP-0 GATE' in line: out.append('GATE')
    return out
for pat in sys.argv[1:]:
    for f in sorted(glob.glob(pat)):
        ev=parse(f)
        if 'GATE' not in ev: continue
        i=ev.index('GATE')
        post=[d for d in ev[i+1:] if isinstance(d,dict)]
        pairs=[(post[j],post[j+1]) for j in range(0,len(post)-1,2)]
        print('==',os.path.basename(f))
        for k,(lock,pw) in enumerate(pairs):
            tl,tp=sum(lock.values()),sum(pw.values())
            esc=tp-pw.get('F',0)
            print(f"  eval{k}: locked F={lock.get('F',0)}/{tl} | pw F-share={pw.get('F',0)/tp:.3f} escapes={esc}")
