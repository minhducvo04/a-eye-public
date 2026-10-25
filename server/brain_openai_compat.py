"""Hosted open-model text brain. Uses the Claude consent switch; never logs provider data."""
import asyncio
import json
import os
from time import perf_counter

import httpx

from server.brain_local import LocalBrain, _canned
from server.llm import LLMClient, LLMResult, _shared_async_client, record_brain
from server.privacy import claude_allowed

TIMEOUT = 30
EXPERT_TIMEOUT = 45


def configured(role='TALKER'):
    return all(os.environ.get('OPENAI_COMPAT_' + key, '').strip()
               for key in ('BASE_URL', 'API_KEY', role + '_MODEL'))


def options(system, user, role, max_tokens, stream):
    # Text scene notes only: open-model providers need not support vision crops.
    return dict(model=os.environ['OPENAI_COMPAT_' + role + '_MODEL'], stream=stream,
                max_tokens=max_tokens, messages=[{'role': 'system', 'content': system},
                                                {'role': 'user', 'content': user}])


def endpoint():
    return os.environ['OPENAI_COMPAT_BASE_URL'].rstrip('/') + '/chat/completions'


def headers():
    return {'Authorization': 'Bearer ' + os.environ['OPENAI_COMPAT_API_KEY']}


class OpenAICompatBrain(LLMClient):
    def local(self):
        return LocalBrain(httpx_client=self._httpx_client)

    async def _chunks(self, payload, timeout):
        client = self._httpx_client or _shared_async_client()
        async with asyncio.timeout(timeout):
            # Recheck immediately at the cloud boundary, including consent revoked mid-call.
            if not claude_allowed():
                return
            async with client.stream('POST', endpoint(), headers=headers(), json=payload,
                                     timeout=timeout) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not claude_allowed():
                        return
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        return
                    chunk = json.loads(data)
                    if 'error' in chunk:
                        raise ValueError('Hosted brain unavailable')
                    yield chunk

    async def stream(self, system, user, *, max_tokens=512, on_backup=None, model=None,
                     images=None, tools=None, on_tool=None):
        if not claude_allowed() or not configured():
            async for delta in self.local().stream(system, user, max_tokens=max_tokens,
                                                   on_backup=on_backup, images=images):
                yield delta
            return
        emitted = False
        try:
            async for chunk in self._chunks(options(system, user, 'TALKER', max_tokens, True), TIMEOUT):
                choices = chunk.get('choices') or []
                delta = choices[0].get('delta', {}).get('content') if choices else None
                if isinstance(delta, str) and delta:
                    record_brain('expert', os.environ['OPENAI_COMPAT_TALKER_MODEL'])
                    emitted = True
                    yield delta
            if emitted:
                return
        except Exception:
            if emitted:
                raise RuntimeError('Hosted brain stream interrupted') from None
            if on_backup:
                await on_backup()
        record_brain('unavailable', 'canned')
        yield _canned(user)

    async def expert(self, system, user, *, model=None, images=None, tools=None, on_tool=None):
        if not claude_allowed() or not configured('EXPERT'):
            return await self.local().expert(system, user, images=images)
        started = perf_counter()
        text, first, usage = '', None, {}
        try:
            async for chunk in self._chunks(options(system, user, 'EXPERT', 2048, True), EXPERT_TIMEOUT):
                usage.update(chunk.get('usage') or {})
                choices = chunk.get('choices') or []
                delta = choices[0].get('delta', {}).get('content') if choices else None
                if isinstance(delta, str) and delta:
                    if first is None:
                        first = (perf_counter() - started) * 1000
                    text += delta
        except Exception:
            raise RuntimeError('Hosted expert unavailable') from None
        return dict(text=text or 'I could not reach a conclusion.', metrics=dict(
            model=os.environ['OPENAI_COMPAT_EXPERT_MODEL'], first_token_ms=first,
            total_ms=(perf_counter() - started) * 1000, input_tokens=usage.get('prompt_tokens', 0),
            output_tokens=usage.get('completion_tokens', 0), cache_read_tokens=0, cache_creation_tokens=0,
            web_searches=0, web_search_errors=0, cost_usd=None,
            search_cost='none', search_cost_excluded=True))

    def complete(self, system, user, *, max_tokens=1024, images=None):
        if not claude_allowed() or not configured():
            return self.local().complete(system, user, max_tokens=max_tokens, images=images)
        started = perf_counter()
        try:
            payload = options(system, user, 'TALKER', max_tokens, False)
            if self._httpx_client is not None:
                response = self._httpx_client.post(endpoint(), headers=headers(), json=payload, timeout=TIMEOUT)
            else:
                with httpx.Client(timeout=TIMEOUT) as client:
                    response = client.post(endpoint(), headers=headers(), json=payload)
            response.raise_for_status()
            text = response.json()['choices'][0]['message']['content']
            if isinstance(text, str) and text.strip():
                return LLMResult(text, 'openai_compat', payload['model'], (perf_counter() - started) * 1000)
        except Exception:
            pass
        return LLMResult(_canned(user), 'canned', 'canned', (perf_counter() - started) * 1000)
