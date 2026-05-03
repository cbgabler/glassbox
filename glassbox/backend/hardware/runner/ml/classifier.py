"""Baseline sklearn classifier for GlassBox."""
from __future__ import annotations
import numpy as np
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import (
    train_test_split, cross_val_score, GroupKFold,
)
from sklearn.metrics import classification_report, confusion_matrix
from features import FEATURE_NAMES, N_FEATURES

CLASSES = ["safe", "timing_leak", "power_leak"]


def train(X: np.ndarray, y: np.ndarray, seed: int = 0, groups=None):
    """Fit the baseline classifier and report held-out + CV accuracy.

    `groups` -- optional 1D array of length len(X). When provided, CV uses
    GroupKFold so all rows that share a group end up in the same fold.
    For GlassBox we pass `groups=df.input_byte` so the 5-repeat measurements
    of one input never get split across folds (which makes ordinary K-fold
    look unrealistically optimistic AND inflates per-fold variance).
    """
    Xtr, Xte, ytr, yte = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=seed
    )
    clf = RandomForestClassifier(n_estimators=200, random_state=seed, n_jobs=-1)
    clf.fit(Xtr, ytr)
    print("\n=== Held-out test set ===")
    print(classification_report(yte, clf.predict(Xte)))
    print("Confusion matrix (rows=true, cols=pred):")
    print(confusion_matrix(yte, clf.predict(Xte), labels=CLASSES))

    print("\n=== Top-10 features by importance ===")
    importances = sorted(zip(FEATURE_NAMES, clf.feature_importances_),
                          key=lambda kv: -kv[1])
    for name, imp in importances[:10]:
        print(f"  {name:>12s}  {imp:.4f}")

    if groups is None:
        cv = cross_val_score(clf, X, y, cv=5, n_jobs=-1)
        print(f"\n5-fold CV accuracy (random split): {cv.mean():.3f} +/- {cv.std():.3f}")
    else:
        gkf = GroupKFold(n_splits=5)
        cv = cross_val_score(clf, X, y, cv=gkf, groups=groups, n_jobs=-1)
        print(f"\n5-fold GroupKFold CV (by input_byte): {cv.mean():.3f} +/- {cv.std():.3f}")
    return clf


def save(clf, path: str = "baseline.joblib"):
    joblib.dump(clf, path)


if __name__ == "__main__":
    from synth import synth_dataset
    X, y = synth_dataset(n_per_class=500)
    clf = train(X, y)
    save(clf)
    print("\nSaved baseline.joblib")