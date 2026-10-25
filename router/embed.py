"""EmbeddingGemma through Ollama (the PC box). Question and context are embedded in one request."""
import os

import httpx

OLLAMA = os.environ.get('ROUTER_OLLAMA_URL', 'http://127.0.0.1:11434')
MODEL = os.environ.get('ROUTER_EMBED_MODEL', 'embeddinggemma:300m')
PREFIX = 'task: classification | query: '


def inputs(text, context):
    """Two strings: the question alone, and the question after its context (or the question again)."""
    question = PREFIX + text
    joined = PREFIX + ' / '.join(list(context) + [text]) if context else question
    return [question, joined]


def embed_many(texts, *, client=None, timeout=10.0):
    own = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        response = client.post(f'{OLLAMA}/api/embed', json={'model': MODEL, 'input': texts, 'keep_alive': '24h'})
        response.raise_for_status()
        return response.json()['embeddings']
    finally:
        if own:
            client.close()


async def aembed_many(texts, *, client, timeout=10.0):
    response = await client.post(f'{OLLAMA}/api/embed', json={'model': MODEL, 'input': texts, 'keep_alive': '24h'},
                                 timeout=timeout)
    response.raise_for_status()
    return response.json()['embeddings']
