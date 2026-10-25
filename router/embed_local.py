"""Mac-CPU embedders in ONNX Runtime, no GPU box needed.

- gemma_local: EmbeddingGemma 300M, int8 ONNX (onnx-community/embeddinggemma-300m-ONNX, Gemma licence)
- minilm: all-MiniLM-L6-v2 (sentence-transformers, Apache-2.0)

Needs onnxruntime, tokenizers and numpy (router/requirements-router.txt). Model files live in
ROUTER_MODEL_DIR (default ~/.cache/a-eye/router/<backend>); fetch them with
`python -m router.embed_local --fetch gemma_local`.
"""
import os
import sys
from functools import lru_cache
from pathlib import Path

SPECS = {
    'gemma_local': {'repo': 'onnx-community/embeddinggemma-300m-ONNX', 'prefix': 'task: classification | query: ',
                    'files': ('onnx/model_quantized.onnx', 'onnx/model_quantized.onnx_data', 'tokenizer.json'),
                    'onnx': 'onnx/model_quantized.onnx', 'output': 'sentence_embedding'},
    'minilm': {'repo': 'sentence-transformers/all-MiniLM-L6-v2', 'prefix': '',
               'files': ('onnx/model.onnx', 'tokenizer.json'), 'onnx': 'onnx/model.onnx', 'output': 'mean'},
}


def model_dir(backend):
    root = os.environ.get('ROUTER_MODEL_DIR')
    return Path(root).expanduser() / backend if root else Path('~/.cache/a-eye/router').expanduser() / backend


@lru_cache(maxsize=2)
def _session(backend):
    import onnxruntime
    from tokenizers import Tokenizer
    folder, spec = model_dir(backend), SPECS[backend]
    tokenizer = Tokenizer.from_file(str(folder / 'tokenizer.json'))
    tokenizer.enable_truncation(128)
    tokenizer.enable_padding()
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = int(os.environ.get('ROUTER_THREADS', '4'))
    return tokenizer, onnxruntime.InferenceSession(str(folder / spec['onnx']), options,
                                                   providers=['CPUExecutionProvider'])


def inputs(text, context, backend='gemma_local'):
    prefix = SPECS[backend]['prefix']
    joined = ' / '.join(list(context) + [text]) if context else text
    return [prefix + text, prefix + joined]


def embed_many(texts, backend='gemma_local'):
    import numpy as np
    tokenizer, session = _session(backend)
    encoded = tokenizer.encode_batch(list(texts))
    ids = np.array([e.ids for e in encoded], dtype=np.int64)
    mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
    names = {i.name for i in session.get_inputs()}
    feeds = {'input_ids': ids, 'attention_mask': mask}
    if 'token_type_ids' in names:
        feeds['token_type_ids'] = np.zeros_like(ids)
    if SPECS[backend]['output'] == 'mean':
        hidden = session.run(None, feeds)[0]
        pooled = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
    else:
        pooled = session.run([SPECS[backend]['output']], feeds)[0]
    pooled = pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
    return pooled.tolist()


def fetch(backend):
    from huggingface_hub import hf_hub_download
    for name in SPECS[backend]['files']:
        print(hf_hub_download(SPECS[backend]['repo'], name, local_dir=str(model_dir(backend))))


if __name__ == '__main__' and '--fetch' in sys.argv:
    fetch(sys.argv[sys.argv.index('--fetch') + 1] if len(sys.argv) > sys.argv.index('--fetch') + 1 else 'gemma_local')
