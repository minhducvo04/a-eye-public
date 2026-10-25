"""Crops for cloud escalation. The cloud brain may see a small crop around one object, never the full frame
(AGENTS.md privacy rule). Frames stay in memory; nothing is written to disk."""
import base64
from io import BytesIO

from PIL import Image

MAX_CROP_SIDE = 512
MAX_CROP_B64 = 200_000   # well under the router's 400,000-byte cloud image cap
MAX_AREA_SHARE = 0.5     # a "crop" that covers most of the frame is a frame: refuse it


def crop_b64(jpeg_b64, bbox, pad=0.15):
    """Return a base64 JPEG crop around a normalized [x, y, w, h] box, or None when it would not be a crop."""
    x, y, w, h = bbox
    x0, y0 = max(0.0, x - w * pad), max(0.0, y - h * pad)
    x1, y1 = min(1.0, x + w * (1 + pad)), min(1.0, y + h * (1 + pad))
    if x1 <= x0 or y1 <= y0 or (x1 - x0) * (y1 - y0) > MAX_AREA_SHARE:
        return None
    with Image.open(BytesIO(base64.b64decode(jpeg_b64))) as image:
        W, H = image.size
        region = image.convert("RGB").crop((round(x0 * W), round(y0 * H), round(x1 * W), round(y1 * H)))
    region.thumbnail((MAX_CROP_SIDE, MAX_CROP_SIDE))
    out = BytesIO()
    region.save(out, format="JPEG", quality=80)
    data = base64.b64encode(out.getvalue()).decode("ascii")
    return data if len(data) <= MAX_CROP_B64 else None


def pick_target(objects, text):
    """The object the question is about: a label word in the user's words wins, else the first object."""
    words = {w.strip(".,?!").lower() for w in (text or "").split()}
    for obj in objects:
        if words & {w.lower() for w in obj["label"].replace("_", " ").split()}:
            return obj
    return objects[0] if objects else None
