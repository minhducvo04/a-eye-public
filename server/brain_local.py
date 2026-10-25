"""Open-model brain: talker, planner and expert through Ollama on our own box, no Claude.

BRAIN=local selects it through make_llm(); anything else keeps LLMClient (Claude first). It keeps
LLMClient's call shapes: stream(system, user, *, max_tokens, on_backup, model, images) yields text, and
expert(system, user, *, model, images) returns {"text", "metrics"} like the Claude expert. Frames and
crops go only to OLLAMA_URL (our box). No prompts, images or provider errors are logged here.
Benchmarks and model choice: docs/results/local-brain.md.
"""
import asyncio
import json
import os
import re
from time import perf_counter

import httpx

from server.llm import LLMClient, LLMResult, _shared_async_client, record_brain

TALKER = 'qwen3.6:35b-a3b-q4_K_M'
EXPERT = 'qwen3.6:35b-a3b-q4_K_M'


def selected():
    return os.environ.get('BRAIN', '').strip().lower() == 'local'


def make_llm(**kwargs):
    """The one switch for the lead: llm = make_llm() wherever LLMClient() is built today."""
    if os.environ.get('BRAIN', '').strip().lower() == 'openai_compat':
        from server.brain_openai_compat import OpenAICompatBrain
        return OpenAICompatBrain(**kwargs)
    return LocalBrain(**kwargs) if selected() else LLMClient(**kwargs)


def talker_model():
    return os.environ.get('BRAIN_TALKER_MODEL', TALKER)


def planner_model():
    return os.environ.get('BRAIN_PLANNER_MODEL') or talker_model()


def expert_model():
    return os.environ.get('BRAIN_EXPERT_MODEL', EXPERT)


def local_name(model, role='talker'):
    """Claude names from the callers map to the local role model; a local tag passes through."""
    if model and not model.startswith('claude-'):
        return model
    if model and role == 'talker' and not model.startswith('claude-haiku'):
        role = 'planner'  # the planner call passes ANTHROPIC_MODEL (a Sonnet/Opus name)
    return {'talker': talker_model, 'planner': planner_model, 'expert': expert_model}[role]()


def vision_models():
    return {os.environ.get('VISION_GROUND_MODEL', 'qwen3.5:9b-q4_K_M'),
            os.environ.get('VISION_CAPTION_MODEL', 'qwen2.5vl:7b-q4_K_M'),
            os.environ.get('VISION_FALLBACK_MODEL', 'qwen2.5vl:7b-q4_K_M')}


def _url():
    return os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/') + '/api/chat'


def _payload(model, system, user, images, max_tokens, think, stream=True):
    message = {'role': 'user', 'content': user}
    if images:
        message['images'] = images
    # 4096 matches LLMClient: a different num_ctx makes Ollama reload a model the app shares.
    options = {'num_ctx': int(os.environ.get('BRAIN_NUM_CTX', '4096'))}
    if model not in vision_models():
        # Measured 2026-10-24: a big model with GPU layers slowed vision grounding by over 20%; CPU only with
        # 8 of 16 cores kept it within noise. 'auto' lets Ollama decide (only when the box is free).
        # Never set these on a vision model: different options make Ollama reload the app's shared runner.
        for key, env, default in (('num_gpu', 'BRAIN_NUM_GPU', '0'), ('num_thread', 'BRAIN_NUM_THREAD', '8')):
            value = os.environ.get(env, default)
            if value != 'auto':
                options[key] = int(value)
    if max_tokens:
        options['num_predict'] = max_tokens
    return dict(model=model, stream=stream, think=think, options=options,
                keep_alive=os.environ.get('BRAIN_KEEP_ALIVE', '30m'),
                messages=[{'role': 'system', 'content': system}, message])


def _canned(user):
    try:
        json.loads(user)
    except ValueError:
        return re.split(r'(?<=[.!?])\s+', user.strip(), maxsplit=1)[0] or 'OK.'
    return '{"agent":"helper","draw":[]}\nSAY: The brain is unavailable. Please try again.'


class LocalBrain(LLMClient):
    """Same interface as LLMClient; every call goes to Ollama."""

    async def _chunks(self, payload, timeout):
        client = self._httpx_client or _shared_async_client()
        async with client.stream('POST', _url(), json=payload, timeout=timeout) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if 'error' in chunk:
                    raise ValueError('Local brain failed')
                yield chunk
                if chunk.get('done'):
                    return

    async def stream(self, system, user, *, max_tokens=512, on_backup=None, model=None, images=None):
        name = local_name(model)
        first_token_s = float(os.environ.get('BRAIN_LOCAL_FIRST_TOKEN_S', '10'))
        emitted = False
        for attempt_images in ([images, None] if images else [None]):
            try:
                async with asyncio.timeout(first_token_s) as deadline:
                    payload = _payload(name, system, user, attempt_images, max_tokens,
                                       os.environ.get('BRAIN_TALKER_THINK', '0') == '1')
                    async for chunk in self._chunks(payload, None):
                        delta = chunk.get('message', {}).get('content', '')
                        if isinstance(delta, str) and delta:
                            record_brain('local', name)
                            deadline.reschedule(None)
                            emitted = True
                            yield delta
                if emitted:
                    return
            except Exception as exc:
                if emitted:
                    raise
                if isinstance(exc, TimeoutError):
                    if on_backup:
                        await on_backup()
                    break
                # A text-only model rejects images: retry once without them.
        record_brain('unavailable', 'canned')
        yield _canned(user)

    async def expert(self, system, user, *, model=None, images=None):
        name = local_name(model, 'expert')
        started = perf_counter()
        if name in vision_models():  # long expert answers on the vision model stall grounding for minutes
            return dict(text='The expert model is not set up, so I will answer as best I can.', metrics=dict(
                model=name, first_token_ms=None, total_ms=0.0, input_tokens=0, output_tokens=0,
                cache_read_tokens=0, cache_creation_tokens=0, web_searches=0, web_search_errors=0,
                cost_usd=0.0, search_cost='none', search_cost_excluded=True, reason='vision_model'))
        first = None
        think = os.environ.get('BRAIN_EXPERT_THINK', '1') == '1'
        counts = dict(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0,
                      web_searches=0)
        text = ''
        tries = [(images, think), (None, think), (None, False)] if images else [(None, think), (None, False)]
        for attempt_images, attempt_think in tries:
            try:
                text = ''
                payload = _payload(name, system, user, attempt_images,
                                   int(os.environ.get('BRAIN_EXPERT_MAX_TOKENS', '4096')), attempt_think)
                async for chunk in self._chunks(payload, None):
                    delta = chunk.get('message', {}).get('content', '')
                    if delta and first is None:
                        first = (perf_counter() - started) * 1000
                    text += delta or ''
                    if chunk.get('done'):
                        counts.update(input_tokens=chunk.get('prompt_eval_count', 0),
                                      output_tokens=chunk.get('eval_count', 0),
                                      eval_ms=chunk.get('eval_duration', 0) / 1e6)
                break
            except (httpx.HTTPError, ValueError):
                continue  # image or think not supported by this model; retry simpler
        return dict(text=text.strip() or 'I could not reach a conclusion.', metrics=dict(
            model=name, first_token_ms=first, total_ms=(perf_counter() - started) * 1000, **counts,
            web_search_errors=0, cost_usd=0.0, search_cost='none', search_cost_excluded=True))

    def complete(self, system, user, *, max_tokens=1024, images=None):
        started = perf_counter()
        name = talker_model()
        try:
            payload = _payload(name, system, user, images, max_tokens, False, stream=False)
            if self._httpx_client is not None:
                response = self._httpx_client.post(_url(), json=payload, timeout=60)
            else:
                with httpx.Client(timeout=60) as client:
                    response = client.post(_url(), json=payload, timeout=60)
            response.raise_for_status()
            text = response.json()['message']['content']
            if isinstance(text, str) and text.strip():
                return LLMResult(text, 'local', name, (perf_counter() - started) * 1000)
        except Exception:
            pass
        return LLMResult(_canned(user), 'canned', 'canned', (perf_counter() - started) * 1000)
