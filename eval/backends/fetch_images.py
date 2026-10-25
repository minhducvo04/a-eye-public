"""Download the public Wikimedia Commons test frames (long edge 1280) into eval/backends/data/.

Licences are the Commons page's LicenseShortName at download time (2026-10-23); see data/sources.json.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image

DATA = Path(__file__).resolve().parent / "data"
UA = {"User-Agent": "a-eye-bench/0.1 (Cal Hacks eval harness)"}
FILES = {
    "pc_ram": ("Lenovo ThinkPad X220 Motherboard (FRU 04W3286) with empty RAM slots.jpg", "CC BY-SA 4.0"),
    "pc_socket": ("LGA 775 CPU Socket.jpg", "CC BY 2.0"),
    "pc_sata": ("ASUS SATA cable 14G000130223.jpg", "CC BY-SA 3.0"),
    "pc_psu": ("ATX Computer power supply unit.jpg", "CC BY-SA 4.0"),
    "pc_gpu": ("PNY GeForce 210 (512 MiB DDR3, PCIe 2.0) 20240807 HOF4752 RAW-Export.png", "CC BY 4.0"),
    "pc_fan": ("CPU socket + fan in Dell Inspiron 3880.jpg", "CC BY-SA 4.0"),
    "cook_onion": ("Red onion on a cutting board.jpg", "CC BY-SA 4.0"),
    "cook_egg": ("HK 煎蛋 pan fried egg October 2025 N13P 01.jpg", "CC0"),
    "cook_knife": ("Chefs knifes on a wooden cutting board.jpg", "CC BY-SA 4.0"),
    "cook_pot": ("Water boiling in a pot on a stove.jpg", "CC0"),
    "cook_cucumber": ("Board, knife and cucumbers.JPG", "CC BY-SA 3.0"),
    "desk_mug": ("Coffee Mug on Desk.jpg", "CC BY-SA 4.0"),
    "desk_setup": ("Brown and beige, Computer desk, Rostov-on-Don, Russia.jpg", "CC BY 4.0"),
    "asm_screwdriver": ("Black screwdriver.png", "CC BY 4.0"),
    "asm_hexkeys": ("Allen long arm hex key set.jpg", "CC BY-SA 3.0"),
    "asm_drill": ("Cordless electric (screw) drill.jpg", "CC0"),
}


def main():
    DATA.mkdir(exist_ok=True)
    sources = {}
    for key, (title, licence) in FILES.items():
        out = DATA / (key + ".jpg")
        page = "https://commons.wikimedia.org/wiki/File:" + urllib.parse.quote(title.replace(" ", "_"))
        sources[key] = {"title": title, "licence": licence, "page": page}
        if out.exists():
            continue
        url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(title.replace(" ", "_")) + "?width=1280"
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
        with Image.open(BytesIO(raw)) as img:
            img = img.convert("RGB")
            scale = 1280 / max(img.size)
            if scale < 1:
                img = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
            img.save(out, "JPEG", quality=88)
            print(key, img.size)
        time.sleep(2)
    (DATA / "sources.json").write_text(json.dumps(sources, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
