"""Local Ollama vision. Images exist only in memory; confidence is heuristic."""
import base64
from difflib import SequenceMatcher
from io import BytesIO
import json
import re
import math
import os
from pathlib import Path
from time import perf_counter

import httpx

CAPTION_TOKENS = 80
from PIL import Image


BOARDS = Path(__file__).resolve().parents[1] / "data" / "boards"

# Native corner order and units, shared by prompts and parsing.
BOX_FORMATS = {
    "qwen2.5vl": ("x1,y1,x2,y2", "pixels"),
    "qwen3.5": ("x1,y1,x2,y2", "0 to 1000"),
    "gemma4:12b": ("x1,y1,x2,y2", "0 to 1000"),
    "gemma4:26b": ("y1,x1,y2,x2", "0 to 1000"),
}


def box_format(model):
    return next((value for family, value in BOX_FORMATS.items() if model.startswith(family)),
                BOX_FORMATS["qwen3.5"])


def parse_json(text):
    """Read the first JSON array/object, including fenced answers with prose."""
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "[{":
            try:
                return decoder.raw_decode(text[index:])[0]
            except ValueError:
                continue
    return None


_LABEL_BOX = re.compile(r'"label"\s*:\s*"([^"]+)"\s*,\s*"bbox_2d"\s*:\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)')
_CAPTION = re.compile(r'"caption"\s*:\s*"([^"]*)"')


def salvage_scene(text):
    """Qwen2.5-VL sometimes emits broken JSON (seen live: a box closed with '}' instead of ']').
    Pull caption and label/box pairs out with regexes instead of losing the whole answer."""
    if not isinstance(text, str):
        return None
    rows = [{"label": m[0], "bbox_2d": [float(v) for v in m[1:]]} for m in _LABEL_BOX.findall(text)]
    caption = _CAPTION.search(text)
    if not rows and not caption:
        return None
    return {"caption": caption.group(1) if caption else "", "objects": rows}


def salvage_box(text):
    """Recover the first labelled box even when its closing bracket is broken."""
    scene = salvage_scene(text)
    return scene["objects"][0] if scene and scene["objects"] else None


def box_rows(answer):
    if isinstance(answer, list):
        return answer
    if isinstance(answer, dict):
        rows = answer.get("objects")
        return rows if isinstance(rows, list) else [answer]
    return []


def box_prompt(label, printed=False, model="qwen3.5"):
    order, units = box_format(model)
    return ('Locate only ' + json.dumps(label) +
            ('. Box the printed text, not the connector' if printed else '') +
            '. Return one JSON {"label":str,"bbox_2d":[' + order + ']} in ' + units + '. '
            'Use the exact label. If absent return {}.')


def normalized(text):
    return "".join(text.casefold().replace("_", "").split())


def headers_for(board_id):
    # Board IDs are filenames, never paths supplied by the caller.
    if not board_id or Path(board_id).name != board_id:
        return []
    try:
        return json.loads((BOARDS / (board_id + ".json")).read_text())["headers"]
    except FileNotFoundError:
        return []


def match_header(label, headers, fuzzy=False):
    if not isinstance(label, str) or not normalized(label):
        return None, 0
    label = normalized(label)
    scores = []
    for header in headers:
        aliases = {normalized(header["name"]), normalized(header.get("silkscreen", header["name"]))}
        if label in aliases:
            return header, .7
        scores.append((max(SequenceMatcher(None, label, alias).ratio() for alias in aliases), header))
    scores.sort(key=lambda entry: entry[0], reverse=True)
    if fuzzy and scores and scores[0][0] >= .8:
        if len(scores) == 1 or scores[0][0] > scores[1][0]:
            return scores[0][1], .5
    return None, 0


def box_normalized(box, width, height, model="qwen2.5vl"):
    if not isinstance(box, list) or len(box) != 4:
        return None
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in box):
        return None
    order, units = box_format(model)
    if order == "y1,x1,y2,x2":
        box = [box[1], box[0], box[3], box[2]]
    if units != "pixels":
        width = height = 1000
    x1, y1, x2, y2 = [max(0, min(1, value / size))
                      for value, size in zip(box, (width, height, width, height))]
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2 - x1, y2 - y1]


class OllamaBackend:
    def __init__(self, *, url=None, model=None, transport=None, client=None):
        self.url = (url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model = model or os.environ.get("VISION_GROUND_MODEL", "qwen3.5:9b-q4_K_M")
        self.caption_model = os.environ.get("VISION_CAPTION_MODEL", "qwen2.5vl:7b-q4_K_M")
        self.fallback_model = os.environ.get("VISION_FALLBACK_MODEL", "qwen2.5vl:7b-q4_K_M")
        self.last_ground_model = self.model
        self.client = client if client is not None else httpx.Client(transport=transport, timeout=8,
            limits=httpx.Limits(keepalive_expiry=1800))
        self.last_timings = {}
        self.timeout = 8
        self.ground_fallback = True

    def _chat(self, body, prompt, num_predict=40, model=None):
        model = model or self.model
        self.last_timings = {}
        with Image.open(BytesIO(base64.b64decode(body["image_b64"]))) as original:
            width, height = original.size
            scale = 1280 / max(width, height)
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            resized = original.convert("RGB").resize(size, Image.Resampling.LANCZOS)
            buffer = BytesIO()
            resized.save(buffer, format="JPEG")
        payload = {
            "model": model, "stream": False, "options": {"temperature": 0, "num_ctx": 4096,
                                                          "num_predict": num_predict},
            "keep_alive": "30m",
            "messages": [{"role": "user", "content": prompt,
                          "images": [base64.b64encode(buffer.getvalue()).decode("ascii")]}],
        }
        if model.startswith(("qwen3.5", "gemma4")):
            payload["think"] = False
        response = self.client.post(self.url + "/api/chat", timeout=self.timeout, json=payload)
        response.raise_for_status()
        self.last_timings = {}
        try:
            data = response.json()
            self.last_timings = {key: data[key] for key in
                ("load_duration", "prompt_eval_duration", "eval_duration") if key in data}
            text = data["message"]["content"]
            answer = parse_json(text)
            # A malformed outer object can parse as an inner array. Prefer salvage
            # when it recovered labelled boxes that the JSON parser did not.
            recovered = salvage_scene(text)
            if recovered and recovered["objects"] and not any(
                    isinstance(row, dict) and "bbox_2d" in row for row in box_rows(answer)):
                answer = recovered
            elif answer is None:
                answer = salvage_box(text) or recovered
        except (ValueError, KeyError, TypeError):
            answer = None
        return answer, size

    def _ground(self, body, label, match, printed=False):
        """Try one native-format box, then exactly one fallback attempt."""
        models = (self.model, self.fallback_model) if self.ground_fallback else (self.model,)
        for attempt, model in enumerate(models):
            self.last_ground_model = model
            try:
                answer, (width, height) = self._chat(
                    body, box_prompt(label, printed=printed, model=model), 60, model=model)
            except httpx.HTTPError:
                if attempt == len(models) - 1:
                    raise
                continue
            for row in box_rows(answer):
                if not isinstance(row, dict):
                    continue
                conf = match(row.get("label"))
                box = box_normalized(row.get("bbox_2d"), width, height, model)
                if conf and box:
                    return box, conf
        return None, 0

    def health(self):
        responses = []
        for endpoint in ("ps", "tags"):
            response = self.client.get(self.url + "/api/" + endpoint, timeout=2)
            response.raise_for_status()
            responses.append(response.json().get("models", []))
        loaded, available = responses
        def is_model(row, model):
            return model in (row.get("name"), row.get("model"))
        current = next((row for row in loaded if is_model(row, self.model)), None)
        gpu = "not_loaded"
        if current is not None:
            vram, size = current.get("size_vram", 0), current.get("size", 0)
            gpu = "cpu" if vram == 0 else "gpu" if vram == size else "partial"
        models = [self.model, self.caption_model]
        return {"ok": all(any(is_model(row, model) for row in available) for model in models), "models": models,
                "gpu": gpu, "fake": False}

    def warmup(self):
        # Low-byte synthetic frame, same grounding resolution and context size.
        buffer = BytesIO()
        Image.new("RGB", (1280, 960), "white").save(buffer, format="JPEG")
        self._chat({"image_b64": base64.b64encode(buffer.getvalue()).decode("ascii")},
                   "Reply OK.", num_predict=1)

    def scene(self, body, scenario):
        started = perf_counter()
        queries = body.get("queries", [])[:3]
        limit = body.get("max_objects", 8)
        result = {"caption": "", "objects": [], "source": "ollama"}
        for query in queries:
            if perf_counter() - started >= 1.5 or len(result["objects"]) >= limit:
                break
            box, conf = self._ground(body, query, lambda label:
                .7 if isinstance(label, str) and normalized(label) == normalized(query) else 0)
            if box:
                result["objects"].append({"id": f'o{len(result["objects"]) + 1}',
                                          "label": query, "bbox": box, "conf": conf})
        # Captions must not consume the budget for starting grounding queries.
        if not queries or body.get("caption", False):
            answer, _ = self._chat(body,
                'Describe the frame in at most 15 words. Return JSON {"caption":str}. No boxes.',
                # 40 tokens cut longer captions mid-string, the JSON never closed, and every frame read
                # "Could not describe the frame." (reported by the local-brain builder).
                CAPTION_TOKENS, model=self.caption_model)
            caption = answer.get("caption") if isinstance(answer, dict) else None
            result["caption"] = caption.strip() if isinstance(caption, str) and caption.strip() else "Could not describe the frame."
        return result

    def identify(self, body, scenario):
        candidates = body["candidates"]
        choices = ", ".join(f'{item} ({item.replace("_", " ")})' for item in candidates)
        answer, _ = self._chat(body, 'Identify the held PC part. Candidates: ' + choices +
                               '. Return JSON {"item": one candidate ID or "unknown", "confidence": 0..1}.')
        if not isinstance(answer, dict):
            return {"item": "unknown", "conf": .3}
        item = answer.get("item")
        conf = answer.get("confidence", .3)
        if type(conf) not in (int, float) or not math.isfinite(conf):
            conf = .3
        return {"item": item if item in candidates else "unknown", "conf": max(0, min(.85, conf))}

    def locate(self, body, scenario):
        headers = headers_for(body["board_id"])
        target, _ = match_header(body["header"], headers)
        if target is None:
            return {"found": False, "conf": 0}
        started = perf_counter()
        candidates = [target] + [header for header in headers if header != target][:2]
        found = {}
        for candidate in candidates:
            if perf_counter() - started >= 1.5:
                break
            label = candidate.get("silkscreen", candidate["name"])
            def match(label):
                header, conf = match_header(label, headers, fuzzy=True)
                return conf if header == candidate else 0
            box, conf = self._ground(body, label, match, printed=True)
            if box:
                found[candidate["name"]] = {"header": candidate["name"], "bbox": box, "conf": conf}
        requested = found.pop(target["name"], None)
        result = {"found": requested is not None, "conf": requested["conf"] if requested else 0,
                  "others": list(found.values())}
        if requested:
            result.update(bbox=requested["bbox"], source="ocr")
        return result

    def verify(self, body, scenario):
        headers = headers_for(body["board_id"])
        target, _ = match_header(body["header"], headers)
        label = target.get("silkscreen", target["name"]) if target else body["header"]
        answer, _ = self._chat(body, 'Is a connector plugged into the header labeled ' + label +
                               '? Which printed label is it next to? Return JSON {"plugged": bool, "label": str}.')
        if (not isinstance(answer, dict) or type(answer.get("plugged")) is not bool
                or not isinstance(answer.get("label"), str)):
            return {"pass": False, "abstain": True, "conf": .3,
                    "reason": "Could not interpret the connection; hold still and move closer."}
        seen, _ = match_header(answer["label"], headers)
        passed = bool(answer["plugged"] and target and seen and target["name"] == seen["name"])
        result = {"pass": passed, "conf": .75,
                  "reason": "Connection appears matched." if passed else "Connection not confirmed at the requested header."}
        result["reason"] += " VLM confidence is below the 0.8 green gate; independent verification is required."
        if seen:
            result["seen_header"] = seen["name"]
        return result


class AutoBackend:
    """Try the PC for every request; only connection failures/timeouts use demo data."""
    def __init__(self, ollama, fake):
        self.ollama, self.fake = ollama, fake

    def _call(self, operation, *args):
        try:
            return getattr(self.ollama, operation)(*args)
        except (httpx.ConnectError, httpx.TimeoutException):
            import logging
            logging.getLogger(__name__).warning("ollama unreachable, using fake")
            return getattr(self.fake, operation)(*args)

    def health(self):
        return self._call("health")

    def identify(self, body, scenario):
        return self._call("identify", body, scenario)

    def scene(self, body, scenario):
        return self._call("scene", body, scenario)

    def locate(self, body, scenario):
        return self._call("locate", body, scenario)

    def verify(self, body, scenario):
        return self._call("verify", body, scenario)
