"""Read-only judge evidence. Authentication is the shared /api middleware."""
import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Request
from server.rewind import registry, identity
from fastapi.responses import JSONResponse

router = APIRouter(prefix='/api/proof')
DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.heic', '.avif', '.tif', '.tiff'}


def picture_count(root=None):
    """Count image-named files (including encrypted crops); never open their contents or follow links."""
    count = 0
    def denied(error):
        raise error
    for directory, _, files in os.walk(root or DATA_DIR, onerror=denied, followlinks=False):
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() in IMAGE_SUFFIXES and not path.is_symlink() and path.is_file():
                count += 1
    return count


@router.get('')
async def status(request: Request, session_id: str = ''):
    call = registry(request.app).get(session_id, identity(request))
    meter = getattr(call, 'cloud_meter', None)
    try:
        count = await asyncio.to_thread(picture_count)
    except OSError:
        count = None  # unreadable is unknown, never a reassuring zero
    return JSONResponse(dict(pictures_saved=count, pictures_sent=meter.pictures_sent if meter else None,
                             **(meter.snapshot() if meter else dict(cloud_ai_calls=None, bytes_to_cloud=None))),
                        headers={'Cache-Control': 'no-store'})
