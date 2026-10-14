"""Rebuild the original Focus Mute icon at Windows icon sizes."""
from pathlib import Path
from PIL import Image, ImageDraw

base = Path(__file__).resolve().parent.parent
im = Image.new('RGBA', (1024, 1024))
d = ImageDraw.Draw(im)
d.rounded_rectangle((40, 40, 984, 984), radius=224, fill='#142D3B')
# A speaker held inside four focus corners, with one clear sound wave.
for points in [((226, 354), (226, 250), (330, 250)),
               ((694, 250), (798, 250), (798, 354)),
               ((226, 670), (226, 774), (330, 774)),
               ((694, 774), (798, 774), (798, 670))]:
    d.line(points, fill='#53D8C2', width=38, joint='curve')
d.rounded_rectangle((315, 437, 421, 587), radius=22, fill='#FFFFFF')
d.polygon(((392, 437), (528, 337), (528, 687), (392, 587)), fill='#FFFFFF')
d.arc((448, 367, 706, 657), -53, 53, fill='#53D8C2', width=45)
im.save(base / 'icon.ico', sizes=[(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)])
im.resize((256,256), Image.Resampling.LANCZOS).save(base / 'assets' / 'icon.png')
