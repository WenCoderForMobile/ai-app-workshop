"""Render the AI program factory mark to Android vectors and legacy bitmaps.
Run from any directory with Python 3 and Pillow installed.
"""
from pathlib import Path
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / 'app/src/main/res'
ART = ROOT / 'design'
PLUGIN_RES = ROOT.parent / 'agent/program_agent/plugin_template/app/src/main/res'
NAVY = '#101D35'
MINT = '#59E3C2'
WHITE = '#F5FCFF'
# A single 108-unit geometry definition drives both vector and bitmap exports.
OPS = [
    ('line', [(40,33),(44,33)], MINT, 2),
    ('line', [(40,40),(44,40)], MINT, 2),
    ('line', [(64,33),(68,33)], MINT, 2),
    ('line', [(64,40),(68,40)], MINT, 2),
    ('line', [(50,25),(50,28)], MINT, 2),
    ('line', [(58,25),(58,28)], MINT, 2),
    ('line', [(50,44),(50,48)], MINT, 2),
    ('line', [(58,44),(58,49)], MINT, 2),
    ('rect', (44,28,64,44,4), WHITE, 0),
    ('line', [(48.5,39),(51.5,32),(54.5,39)], NAVY, 1.7),
    ('line', [(49.8,36.5),(53.2,36.5)], NAVY, 1.5),
    ('line', [(59,32),(59,39)], NAVY, 1.8),
    ('polygon', [(29,55),(43,46),(43,55),(57,47),(57,55),(77,55),(77,76),(29,76)], MINT, 0),
    ('line', [(41,62),(36,66),(41,70)], NAVY, 2.5),
    ('line', [(56,61),(52,71)], NAVY, 2.5),
    ('line', [(66,62),(71,66),(66,70)], NAVY, 2.5),
]

def path(op, points):
    if op == 'rect':
        x,y,r,b,c=points
        return f'M{x+c},{y} H{r-c} Q{r},{y} {r},{y+c} V{b-c} Q{r},{b} {r-c},{b} H{x+c} Q{x},{b} {x},{b-c} V{y+c} Q{x},{y} {x+c},{y} Z'
    return 'M'+' L'.join(f'{x},{y}' for x,y in points)+(' Z' if op=='polygon' else '')

def vector():
    lines=['<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">']
    for op,points,color,width in OPS:
        attrs=f'android:pathData="{path(op,points)}"'
        if op=='line': attrs+=f' android:fillColor="#00000000" android:strokeColor="{color}" android:strokeWidth="{width}" android:strokeLineCap="round" android:strokeLineJoin="round"'
        else: attrs+=f' android:fillColor="{color}"'
        lines.append('    <path '+attrs+' />')
    lines.append('</vector>')
    return '\n'.join(lines)+'\n'

def render(size, circle=False):
    scale=12
    im=Image.new('RGBA',(108*scale,108*scale),NAVY)
    d=ImageDraw.Draw(im)
    for op,pts,color,width in OPS:
        if op=='rect':
            x,y,r,b,c=pts
            d.rounded_rectangle((x*scale,y*scale,r*scale,b*scale), radius=c*scale,fill=color)
        elif op=='polygon': d.polygon([(x*scale,y*scale) for x,y in pts], fill=color)
        else:
            coords=[(x*scale,y*scale) for x,y in pts]
            d.line(coords,fill=color,width=round(width*scale),joint='curve')
            radius=width*scale/2
            for x,y in coords: d.ellipse((x-radius,y-radius,x+radius,y+radius),fill=color)
    im=im.crop((18*scale,18*scale,90*scale,90*scale))
    mask=Image.new('L',im.size,0); m=ImageDraw.Draw(mask)
    bounds=(0,0,im.width-1,im.height-1)
    if circle: m.ellipse(bounds,fill=255)
    else: m.rounded_rectangle(bounds,radius=im.width*.23,fill=255)
    im.putalpha(mask)
    return im.resize((size,size),Image.Resampling.LANCZOS)

if __name__=='__main__':
    ART.mkdir(exist_ok=True)
    (RES/'drawable-v24/ic_launcher_foreground.xml').write_text(vector())
    (RES/'drawable/ic_launcher_background.xml').write_text(f'<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108"><path android:fillColor="{NAVY}" android:pathData="M0,0 H108 V108 H0 Z" /></vector>\n')
    for density,size in [('mdpi',48),('hdpi',72),('xhdpi',96),('xxhdpi',144),('xxxhdpi',192)]:
        for circle in (False,True):
            name='ic_launcher_round' if circle else 'ic_launcher'
            bitmap = render(size,circle)
            bitmap.save(RES/f'mipmap-{density}/{name}.webp',lossless=True)
            app_name = 'ic_factory_app_round' if circle else 'ic_factory_app'
            # Application metadata uses PNG so APK inspectors need not render adaptive XML.
            bitmap.save(RES/f'mipmap-{density}/{app_name}.png')
            target = PLUGIN_RES/f'mipmap-{density}'
            target.mkdir(parents=True, exist_ok=True)
            bitmap.save(target/f'{app_name}.png')
            if not circle:
                bitmap.save(RES/f'mipmap-{density}/ic_factory_launcher.png')
    (RES/'mipmap-anydpi-v26/ic_factory_launcher.xml').write_text('''<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
</adaptive-icon>
''')
    render(512).save(ART/'ai-program-factory-icon.png')
    render(512,True).save(ART/'ai-program-factory-icon-round.png')
    # Editable design master.
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="18 18 72 72"><rect x="18" y="18" width="72" height="72" rx="16.5" fill="{NAVY}"/>']
    for op,pts,color,width in OPS:
        attrs=f'fill="{color}"' if op!='line' else f'fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"'
        svg.append(f'<path d="{path(op,pts)}" {attrs}/>')
    (ART/'ai-program-factory-icon.svg').write_text('\n'.join(svg+['</svg>']))
    for p in (RES/'drawable-v24/ic_launcher_foreground.xml',RES/'drawable/ic_launcher_background.xml'):
        ET.parse(p)
    print('Generated host launcher icons, PNG application icons, plugin template icons and previews.')
