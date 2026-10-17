# Person 2 — six-hour flood crossing model

This folder implements the ML workstream independently of the API. The first
model is scoped to the Meramec River near Eureka (USGS `07019000`), with the
published minor flood threshold of 19 ft and a six-hour horizon. It predicts a
new upward crossing; observations already above 19 ft are treated as an
ongoing flood and excluded from new-crossing labels.

The repository already contains USGS replay fixtures for this station,
including event-free controls. `train_replays.py` selects only Eureka fixtures
whose threshold is exactly 19 ft. It does not mix in Wimberley observations,
which have a different threshold and river behavior.

## Train, compare, and save

From `backend/ml/` in a Python environment that supports scikit-learn:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python train_replays.py --train-end 2024-01-01T00:00:00Z
```

The default cutoff reserves 2024-and-later examples for the chronological
test period. A six-hour purge before the cutoff avoids training on labels whose
future outcome window overlaps the test period. The script prints exact
metrics and saves `riverguard_model.joblib` plus `model_metadata.json` under
`backend/artifacts/ml/`. Artifacts are generated locally and should not be
committed; regenerate them in the LinuxONE environment for its package
versions.

The classifier is a regularised scikit-learn
`HistGradientBoostingClassifier`. Features are river stage, changes over 1, 3,
and 6 hours, and recent mean, variability, and rate of rise. Feature creation
uses only observations at or before each timestamp, excludes stale or
insufficient history, and drops incomplete future-label windows.

Reported comparisons include a persistence baseline and a six-hour linear
trend extrapolation baseline. The report includes precision, recall, F1,
false-positive and false-negative counts, average precision, ROC AUC, and
Brier score. Forecast origins overlap, and the held-out set has few independent
flood events; these metrics are descriptive evidence, not proof of general
forecasting performance. If the temporal split lacks both classes, the script
stops rather than emitting misleading scores.

## Current replay result

The model was trained and evaluated locally on the committed fixtures using a
2024-01-01 UTC cutoff. Training contained 990 forecast origins from the
2015/2017 Eureka event windows. The held-out period contained 6,200 origins,
including 48 positive six-hour windows across the November 2024 and April 2025
flood replays and the January/February 2024 control replays.

| Method | Precision | Recall | F1 | False alarms | Missed positive windows |
|---|---:|---:|---:|---:|---:|
| HistGradientBoosting | 0.661 | 0.813 | 0.729 | 20 | 9 |
| Persistence | 0.000 | 0.000 | 0.000 | 0 | 48 |
| Six-hour trend extrapolation | 0.511 | 1.000 | 0.676 | 46 | 0 |

The model's average precision was 0.777, ROC AUC 0.998, and Brier score 0.0044.
These row-level scores are heavily influenced by overlapping forecast windows
and only two held-out flood events. They do not establish that predicted
probabilities are calibrated or that the model generalizes to other rivers or
events. The trend baseline catches every positive window but raises more than
twice as many false alarms. Review the event-level replay report before using
these numbers in a presentation. Only the dependency-based local run has been
verified so far; regenerate and re-evaluate in LinuxONE before serving this
artifact.

## Inference and handoff

```python
from model import load_artifact, predict_risk

model = load_artifact("backend/artifacts/ml")
result = predict_risk(observations, "2024-11-07T18:00:00Z", model=model)
```

Input columns are `timestamp` (timezone-aware or parseable UTC values) and
`stage_ft`. `predict_risk()` filters out every row after `as_of`. Without a
validated loaded model, it returns `crossing_probability: null` and a named
fallback; it never emits a fabricated probability. Person 3 can integrate
model loading and serving after confirming LinuxONE dependency compatibility.
