"""Hash-pinned add-only STEP attachment specifications. No UI automation."""
from pathlib import Path,PureWindowsPath
import hashlib,math,re

def text(v,name):
    if not isinstance(v,str) or not v or any(c in v for c in '|\r\n\0'):raise ValueError(name+' must be nonempty delimiter-free text')
    return v

def number(v,name):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>10000:raise ValueError(name+' must be finite, abs<=10000')
    return format(v,'.12g')

def path(v,suffixes):
    v=text(v,'path');p=PureWindowsPath(v)
    if not p.is_absolute() or '..' in p.parts or p.suffix.lower() not in suffixes or '/' in v:raise ValueError('canonical absolute Windows path required')
    if not Path(v).is_file():raise ValueError('file does not exist: '+v)
    return v

def build_models_spec(pcb_path,models):
    lines=['PCB|'+path(pcb_path,{'.pcbdoc'})];seen=set()
    if not isinstance(models,list) or not 1<=len(models)<=128:raise ValueError('models must contain 1..128 records')
    fields={'designator','model_path','sha256','x_mm','y_mm','rotation_deg','side','footprint','bounds_xy_mm','color'}
    for m in models:
        if not isinstance(m,dict) or set(m)!=fields:raise ValueError('model fields must be exactly '+','.join(sorted(fields)))
        ref=text(m['designator'],'designator');fp=text(m['footprint'],'footprint')
        if ref.upper() in seen:raise ValueError('duplicate designator')
        seen.add(ref.upper());p=path(m['model_path'],{'.step','.stp'})
        sha=text(m['sha256'],'sha256').lower()
        if not re.fullmatch('[0-9a-f]{64}',sha) or hashlib.sha256(Path(p).read_bytes()).hexdigest()!=sha:raise ValueError('model SHA256 mismatch')
        if m['side'] not in ('top','bottom'):raise ValueError('side must be top or bottom')
        b=m['bounds_xy_mm']
        if not isinstance(b,list) or len(b)!=4:raise ValueError('bounds_xy_mm requires xmin,ymin,xmax,ymax in board axes relative to component origin')
        nums=[number(v,'bound') for v in b]
        if b[0]>=b[2] or b[1]>=b[3] or max(map(abs,b))>100:raise ValueError('invalid model bounds')
        col=m['color']
        if type(col) is not int or not 0<=col<=16777215:raise ValueError('color must be RGB integer')
        lines.append('|'.join(['MODEL',ref,p,sha,number(m['x_mm'],'x'),number(m['y_mm'],'y'),number(m['rotation_deg'],'rotation'),m['side'],fp,*nums,str(col)]))
    return '\n'.join(lines)+'\n'
