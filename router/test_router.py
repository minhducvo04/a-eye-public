"""route() and the data loader. Hermetic: a fake embedder stands in for EmbeddingGemma."""
import json

import pytest

from router import data, route as route_mod
from router.labels import LABELS
from router.route import classify, load_model, route


def tiny_model(tmp_path, threshold=0.6):
    # 2-dim features per embedding (4 total); class k fires on feature k.
    model = {'backend': 'minilm', 'head': 'lr', 'labels': list(LABELS), 'threshold': threshold,
             'layers': [{'w': [[5, 0, 0, 0], [0, 5, 0, 0], [0, 0, 5, 0]], 'b': [0, 0, 0]}]}
    path = tmp_path / 'model.json'
    path.write_text(json.dumps(model))
    return str(path)


def fake_embed(vector):
    seen = []

    def call(texts):
        seen.append(texts)
        return [vector[:2], vector[2:]]
    call.seen = seen
    return call


def test_route_returns_label_and_confidence(tmp_path):
    result = route('where did the screw go', [], model_path=tiny_model(tmp_path), embed=fake_embed([0, 1, 0, 0]))
    assert result['label'] == 'past'
    assert 0.6 < result['confidence'] <= 1
    assert set(result['probs']) == set(LABELS)


def test_route_unsure_below_threshold(tmp_path):
    result = route('hm', [], model_path=tiny_model(tmp_path), embed=fake_embed([0.1, 0.1, 0.1, 0]))
    assert result['label'] == 'unsure'
    assert result['confidence'] < 0.6


def test_route_embedder_failure_is_unsure(tmp_path):
    def broken(texts):
        raise ConnectionError('box down')
    result = route('is this right', [], model_path=tiny_model(tmp_path), embed=broken)
    assert result['label'] == 'unsure' and result['error'] == 'ConnectionError'


def test_route_uses_last_two_turns_as_strings_or_dicts(tmp_path, monkeypatch):
    captured = {}

    def inputs(text, context):
        captured['context'] = context
        return [text, ' / '.join(context + [text])]
    monkeypatch.setattr(route_mod, '_embedder', lambda backend: (inputs, None))
    turns = ['old', {'role': 'assistant', 'text': 'Reseat the 8-pin.'}, {'role': 'user', 'content': 'Done, nothing.'}]
    route('still not working', turns, model_path=tiny_model(tmp_path), embed=fake_embed([0, 0, 1, 0]))
    assert captured['context'] == ['Reseat the 8-pin.', 'Done, nothing.']


def test_classify_mlp_forward_pass():
    model = {'labels': ['now', 'past', 'expert'],
             'layers': [{'w': [[1, -1]], 'b': [0]}, {'w': [[1], [0], [-1]], 'b': [0, 0, 0]}]}
    probs = classify([2, 0], model)          # hidden relu(2) = 2 -> logits [2, 0, -2]
    assert max(probs, key=probs.get) == 'now' and abs(sum(probs.values()) - 1) < 1e-9


def test_shipped_model_is_well_formed():
    model = load_model()
    assert sorted(model['labels']) == sorted(LABELS)
    assert 0.34 <= model['threshold'] < 1
    assert len(model['layers'][0]['w']) == 3 and len(model['layers'][0]['w'][0]) == 1536


def test_loader_skips_comments_and_checks_rows(tmp_path):
    path = tmp_path / 'x.jsonl'
    path.write_text('# note\n\n{"text": " hi ", "label": "now", "context": [{"text": "a"}]}\n')
    assert data.load(path) == [{'text': 'hi', 'label': 'now', 'context': ['a']}]
    for bad in ({'text': '', 'label': 'now'}, {'text': 'x', 'label': 'later'},
                {'text': 'x', 'label': 'now', 'context': ['a', 'b', 'c']}):
        path.write_text(json.dumps(bad) + '\n')
        with pytest.raises(ValueError):
            data.load(path)


def test_missing_file_loads_empty(tmp_path):
    assert data.load(tmp_path / 'none.jsonl') == []


def test_shipped_data_is_valid_and_disjoint():
    train, held = data.load(data.TRAIN), data.heldout()
    assert len(train) >= 2500 and len(held) >= 100
    assert {r['label'] for r in train} == set(LABELS) == {r['label'] for r in held}
    assert not {r['text'].lower() for r in train} & {r['text'].lower() for r in held}
