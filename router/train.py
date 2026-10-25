"""Train the router head on cached embeddings and export it as plain JSON (no numpy needed at inference).

Usage: python -m router.train --backend gemma_local|gemma|minilm --cache DIR [--head lr|mlp]
Needs numpy and scikit-learn (training only). Tuning uses 5-fold cross-validation on train.jsonl only; the
held-out set is never read here. The "unsure" threshold is the lowest top-class probability at which
out-of-fold accuracy on the kept rows reaches TARGET.
"""
import argparse
import json
from pathlib import Path

from router.data import TRAIN, load
from router.features import cached
from router.labels import LABELS

TARGET = 0.99
OUT = Path(__file__).resolve().parent / 'model.json'


def make(head, c):
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    if head == 'mlp':
        return MLPClassifier(hidden_layer_sizes=(64,), alpha=c, max_iter=400, early_stopping=True, random_state=0)
    return LogisticRegression(C=c, max_iter=3000, class_weight='balanced')


def threshold(probs, y):
    import numpy as np
    top, pred = probs.max(1), probs.argmax(1)
    for t in np.arange(0.34, 0.99, 0.01):
        keep = top >= t
        if keep.sum() and (pred[keep] == y[keep]).mean() >= TARGET:
            return round(float(t), 2), float(keep.mean())
    return 0.99, float((top >= 0.99).mean())


def export(model, head, backend, thresh, cv):
    if head == 'mlp':
        layers = [{'w': w.T.tolist(), 'b': b.tolist()} for w, b in zip(model.coefs_, model.intercepts_)]
    else:
        layers = [{'w': model.coef_.tolist(), 'b': model.intercept_.tolist()}]
    classes = [LABELS[i] for i in model.classes_]
    return {'backend': backend, 'head': head, 'labels': classes, 'threshold': thresh, 'layers': layers, 'cv': cv}


def main():
    import numpy as np
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', default='gemma_local')
    parser.add_argument('--cache', required=True)
    parser.add_argument('--head', default='lr')
    parser.add_argument('--out', default=str(OUT))
    args = parser.parse_args()
    rows = load(TRAIN)
    x = cached(rows, args.backend, args.cache)
    y = np.array([LABELS.index(r['label']) for r in rows])
    folds = StratifiedKFold(5, shuffle=True, random_state=0)
    grid = [0.3, 1, 3, 10, 30] if args.head == 'lr' else [1e-4, 1e-3, 1e-2]
    best = None
    for c in grid:
        probs = cross_val_predict(make(args.head, c), x, y, cv=folds, method='predict_proba')
        pred = probs.argmax(1)
        macro = np.mean([(pred[y == k] == k).mean() for k in range(len(LABELS))])
        print(f'c={c}: cv acc {(pred == y).mean():.3f} macro recall {macro:.3f}')
        if best is None or macro > best[0]:
            best = (macro, c, probs)
    macro, c, probs = best
    thresh, coverage = threshold(probs, y)
    cv = {'c': c, 'acc': round(float((probs.argmax(1) == y).mean()), 4), 'macro_recall': round(float(macro), 4),
          'threshold_coverage': round(coverage, 4), 'target_kept_acc': TARGET}
    print('chosen', cv, 'threshold', thresh)
    model = make(args.head, c).fit(x, y)
    Path(args.out).write_text(json.dumps(export(model, args.head, args.backend, thresh, cv)) + '\n')
    print('wrote', args.out)


if __name__ == '__main__':
    main()
