"""Deterministic demo backend; image data is never decoded or retained."""
from copy import deepcopy
import json
from pathlib import Path


SCENARIOS = {"ok", "wrong_header", "blurry", "unknown_item"}


class FakeBackend:
    def __init__(self):
        self.fixtures = json.loads(Path(__file__).with_name("fixtures.json").read_text())

    def health(self):
        return {"ok": True, "models": ["fake"], "fake": True}

    def scene(self, body, scenario):
        if scenario == "blurry":
            return {"caption": "too blurry to see", "objects": [], "source": "fake"}
        queries = body.get("queries", [])
        labels = queries + [label for label in ["motherboard", "fan cable", "table"]
                            if label not in queries]
        objects = [
            {"id": f"o{index + 1}", "label": label,
             "bbox": [.05 + (index % 4) * .24, .05 + (index // 4) * .3, .18, .2],
             "conf": .7 if index < len(queries) else .5}
            for index, label in enumerate(labels[:body.get("max_objects", 8)])
        ]
        return {"caption": "A motherboard and fan cable sit on a table.",
                "objects": objects, "source": "fake"}

    def identify(self, body, scenario):
        if scenario == "unknown_item":
            return {"item": "unknown", "conf": 0.2}
        return {"item": body["candidates"][0], "conf": 0.9, "bbox": [0.1, 0.7, 0.2, 0.2]}

    def locate(self, body, scenario):
        if scenario == "blurry":
            return {"found": False, "conf": 0, "blur": True}
        board = self.fixtures.get(body["board_id"])
        header = body["header"]
        if board is None or header not in board["headers"]:
            return {"found": False, "conf": 0}
        headers = board["headers"]
        result = {
            "found": True, "bbox": list(headers[header]), "source": "fake", "conf": 0.86,
            "blur": False, "glare": False,
            "others": [{"header": name, "bbox": list(box), "conf": 0.86}
                       for name, box in headers.items() if name != header],
        }
        if header == "F_PANEL" and "want_pins" in body:
            result["pins"] = deepcopy(board["f_panel_pins"])
        return result

    def verify(self, body, scenario):
        if scenario == "blurry":
            return {"pass": False, "abstain": True, "conf": 0.3,
                    "reason": "Too blurry. Hold still and move closer."}
        if scenario == "wrong_header":
            return {"pass": False, "conf": 0.9, "seen_header": "SYS_FAN1",
                    "reason": "Wrong-header demo: connection seen on SYS_FAN1."}
        return {"pass": True, "conf": 0.9, "seen_header": body["header"],
                "reason": "Fake backend: connection accepted."}
