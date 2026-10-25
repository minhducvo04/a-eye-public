"""Brain eval: replay text-only cases through the real run_turn and score each turn.

Runs the single-brain path (no planner): one streamed reply, a JSON header line, then
SAY: speech. That is the path a local model serves when there is no Anthropic key or
LOCAL_ONLY=1. Each case's scene is loaded as the session's cached scene. General's
pointer queries use a fixture-only grounding oracle, not a real vision call. This
measures target selection, not visual accuracy. Market searches are recorded, never sent.

    python -m eval.brain_turns --model local:qwen3.5:9b-q4_K_M --model local:<small model>
"""
import argparse
import asyncio
import csv
import json
import os
import sys
from contextlib import aclosing
from pathlib import Path
from time import monotonic
from unittest.mock import patch

from contracts import is_valid
from eval.brain_score import markdown, score, summarize
from server import packs
from server.agent import Session, run_turn, safe_speech, stream_header
from server.llm import LLMClient
from server.pointer import TargetTag

CASES_PATH = Path(__file__).resolve().parent / 'brain_cases.json'
FRAME = dict(v=2, type='frame', session_id='eval', seq=1, ts_ms=1, w=640, h=480, jpeg_b64='synthetic')
PROVIDERS = ('local', 'claude')
MODEL_ENV = dict(local='LOCAL_LLM_MODEL', claude='ANTHROPIC_TURN_MODEL')
UNAVAILABLE = 'The brain is unavailable'  # LLMClient.stream's last-resort reply
CSV_FIELDS = ('case_id', 'tag', 'provider', 'first_token_ms', 'turn_ms', 'passed', 'format_ok', 'grounded',
              'target_ok', 'market_ok', 'brief', 'say', 'error')


class RecordingLLM:
    """Passes the stream through and keeps the raw reply, which run_turn would otherwise hide."""

    def __init__(self, client, session):
        self.client = client
        self.session = session
        self.raw = ''
        self.called = False

    async def stream(self, system, user, **kwargs):
        self.called = True
        async with aclosing(self.client.stream(system, user, **kwargs)) as stream:
            async for delta in stream:
                self.raw += delta
                yield delta
        # The text-only oracle holds the same fixture view throughout the answer.
        # Present it as a fresh frame for pointing; model latency is scored separately.
        self.session.frame_time = monotonic()


class FixtureVision:
    """Explicit stand-in for local grounding; no frame or request leaves the eval."""
    def __init__(self, scene):
        self.scene = scene

    async def call(self, operation, payload):
        labels = {q.casefold() for q in payload.get('queries', [])}
        # Simulate a successful fresh single-label Qwen result (.7), independently of
        # the old cached scene's confidence. This oracle scores the talker's name only.
        return dict(self.scene, source='ollama', objects=[
            dict(obj, conf=.7) for obj in self.scene['objects'] if obj['label'].casefold() in labels])


def parse_model(spec: str) -> tuple[str, str]:
    provider, _, model = spec.partition(':')
    if provider not in PROVIDERS or not model:
        raise argparse.ArgumentTypeError(f'use provider:model with provider in {PROVIDERS}, got {spec!r}')
    return provider, model


def use_model(provider: str, model: str) -> None:
    os.environ['LLM_PROVIDER'] = provider
    os.environ[MODEL_ENV[provider]] = model


def session_for(case: dict) -> Session:
    session = Session(f"eval-{case['case_id']}", pack=packs.get(case['pack']),
                      power_off=case.get('power_off', True), frame=dict(FRAME))
    session.history = list(case.get('history', []))
    session.scene, session.scene_seq, session.scene_time = case['scene'], FRAME['seq'], monotonic()
    return session


def header_facts(raw: str) -> dict:
    parsed = stream_header(TargetTag().feed(raw))
    if parsed is None:
        return dict(header=None, format_ok=False)
    header, rest = parsed
    # The prompt asks for say on the SAY: line, never inside the header.
    strict = isinstance(header, dict) and 'say' not in header and is_valid('brain_turn', dict(header, say='x'))
    return dict(header=header if isinstance(header, dict) else None, format_ok=strict and bool(safe_speech(rest)))


def message_facts(messages: list, scene=None) -> dict:
    drawn, speech, metrics = [], [], {}
    for msg in messages:
        if msg['type'] == 'agent_state' and msg['agent'] == 'shopper' and msg['state'] == 'thinking':
            break  # the code's shopper lines follow; a model-chosen shopper agent still speaks first
        if msg['type'] == 'draw':
            for op in msg['ops']:
                if 'anchor' not in op:
                    continue
                anchor = op['anchor']
                object_id = anchor['object_id']
                if scene and object_id.startswith('pointer-'):
                    object_id = next((obj['id'] for obj in scene['objects'] if obj['bbox'] == anchor['bbox']), object_id)
                drawn.append(object_id)
        elif msg['type'] == 'say':
            speech.append(msg['text'])
    for msg in messages:
        if msg['type'] == 'metrics':
            metrics = msg
    return dict(drawn=drawn, speech=' '.join(speech), first_token_ms=metrics.get('brain_first_token_ms', 0),
                turn_ms=metrics.get('turn_ms', 0))


async def ask(case: dict, client, provider: str) -> dict:
    session = session_for(case)
    llm, searched = RecordingLLM(client, session), []

    def record_search(query):
        searched.append(query['category'])
        raise RuntimeError('eval: market search recorded, not sent')

    with patch('server.agent.search', record_search):
        messages = await run_turn(session, case['text'], vision=FixtureVision(case['scene']), llm=llm)
    if not llm.called:
        raise ValueError(f"case {case['case_id']} never reached the brain (a code gate answered)")
    return dict(header_facts(llm.raw), **message_facts(messages, case['scene']), market=searched[0] if searched else None,
                provider='canned' if UNAVAILABLE in llm.raw else provider)


async def run_model(cases: list, client, provider: str, *, warmup: bool = True) -> list:
    """All cases share one event loop, so the client keeps its warm connection pool."""
    if warmup and cases:
        await ask(cases[0], client, provider)  # loads the model; the cold call is not scored
    return [score(case, await ask(case, client, provider)) for case in cases]


def write_rows(rows: list, path: Path) -> None:
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def safe_name(label: str) -> str:
    return ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in label)


def evaluate(cases: list, models: list, out_dir: Path | None, *, client=None, warmup: bool = True) -> dict:
    client = client or LLMClient()
    results = {}
    for provider, model in models:
        label = f'{provider}:{model}'
        use_model(provider, model)
        rows = asyncio.run(run_model(cases, client, provider, warmup=warmup))
        results[label] = summarize(rows)
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            write_rows(rows, out_dir / f'brain-{safe_name(label)}.csv')
    if out_dir is not None:
        (out_dir / 'brain.md').write_text(markdown(results))
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='python -m eval.brain')
    parser.add_argument('--model', action='append', type=parse_model, required=True,
                        help='provider:model, repeatable, e.g. local:qwen3.5:9b-q4_K_M')
    parser.add_argument('--cases', type=Path, default=CASES_PATH)
    parser.add_argument('--tag', action='append', help='only run cases with this tag (repeatable)')
    parser.add_argument('--out', type=Path, default=Path('eval/out'))
    parser.add_argument('--no-warmup', action='store_true')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = json.loads(args.cases.read_text())
    if args.tag:
        cases = [case for case in cases if case.get('tag') in args.tag]
    results = evaluate(cases, args.model, args.out, warmup=not args.no_warmup)
    print(markdown(results))
    fell_back = [label for label, s in results.items() if 'canned' in s['providers']]
    if fell_back:
        print('WARNING: the brain was unavailable for some cases (check OLLAMA_URL, the key, or '
              'BRAIN_FIRST_TOKEN_S): ' + ', '.join(fell_back), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
