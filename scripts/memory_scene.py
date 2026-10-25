"""Synthetic table with three red cups and a key ring, for the local memory demo and real runs (no camera)."""
import base64
import io
import random

from PIL import Image, ImageDraw

CUP_X = (90, 270, 450)


def table(keys_at=None, seed=0):
    """keys_at: None (hidden), 'left', 'middle' or 'right' (keys beside that cup)."""
    rng = random.Random(seed)
    image = Image.new('RGB', (640, 480), (196, 160, 120))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 640, 150], fill=(235, 232, 225))                 # wall
    for x in CUP_X:
        draw.polygon([(x, 220), (x + 110, 220), (x + 95, 360), (x + 15, 360)], fill=(200, 30, 35))
        draw.ellipse([x, 205, x + 110, 235], fill=(150, 20, 25))
    if keys_at:
        x = CUP_X[['left', 'middle', 'right'].index(keys_at)] + 40
        draw.ellipse([x, 380, x + 46, 426], outline=(170, 170, 175), width=6)         # ring
        draw.rectangle([x + 40, 396, x + 110, 410], fill=(190, 190, 195))             # key 1 blade
        draw.ellipse([x + 100, 386, x + 130, 420], fill=(190, 190, 195))              # key 1 head
        for tooth in range(4):
            draw.rectangle([x + 50 + tooth * 12, 410, x + 56 + tooth * 12, 418], fill=(190, 190, 195))
        draw.rectangle([x + 10, 420, x + 24, 470], fill=(200, 170, 60))               # brass key 2
    for _ in range(300):
        draw.point((rng.randrange(640), rng.randrange(480)), fill=(170, 135, 100))
    out = io.BytesIO()
    image.save(out, format='JPEG', quality=85)
    return base64.b64encode(out.getvalue()).decode()
