# Person 2 — flood-risk ML and replay evaluation

## What this implements

`model.py` builds a six-hour prediction for a new upward crossing of a gauge's
minor-flood threshold. It uses only observations available at the prediction
time. Already-flooded readings are treated as an ongoing state, and incomplete
or gappy windows are excluded. Features are expressed as a fraction of the
station's threshold (stage, recent changes, recent mean and variability), so
the pipeline can compare the 19 ft Eureka station with the 13 ft Wimberley
station without treating their absolute river heights as equivalent.

`train_replays.py` reads every committed replay JSON in `backend/data/replays`.
It builds labels separately for each replay, preserving station, event/control
role and replay ID. That prevents feature or outcome windows from crossing
between separate files. It then runs three time-ordered evaluations:

1. **Primary Eureka model:** train before 2024-01-01 UTC; test on later Eureka
   replays, including two flood events and two control months.
2. **Wimberley diagnostic:** train before 2019-01-01 UTC; test on the later
   May 2019 flood and two 2024 control months.
3. **Cross-gauge diagnostic:** combine all stations, train before 2018-01-01
   UTC and test later events and controls, including the July 2025 Guadalupe
   flood replay.

Each split purges the six hours immediately before the test boundary, because
those training labels would otherwise look into the test period. The model
comparison is `HistGradientBoostingClassifier` against persistence and a
six-hour linear trend extrapolation. We save only the Eureka model because the
Wimberley diagnostic performs poorly and the combined model transfers poorly.

## Reproduce the run

From the repository root, train through the canonical `ml` package import
used by the serving image:

```sh
python3 -m venv backend/ml/.venv
. backend/ml/.venv/bin/activate
python -m pip install -r backend/ml/requirements.txt
PYTHONPATH=backend:. python -m ml.train_replays
```

Run the ML regression suite from the repository root with the same package
identity:

```sh
PYTHONPATH=backend:. python -m unittest discover -s backend/ml/tests -v
```

The script prints exact metrics, source file checksums, label counts and
per-replay results. It writes the Eureka estimator and metadata to
`backend/artifacts/ml/USGS-07019000/`; artifacts are ignored by Git. Regenerate
the model inside LinuxONE with its installed package versions before serving
it there.

## Evaluation status

Run `PYTHONPATH=backend:. python -m ml.train_replays` to regenerate counts and
metrics after applying the shared input validator. The command reports
excluded training windows by reason alongside metrics, source checksums and
replay counts. Artifacts record `ml.model.FittedRiskModel` as the canonical
serialized class path, matching the service's `from ml.model import ...`
loader. Metrics from
earlier runs are not comparable because they did not enforce the full six-hour
lookback quality contract. Report replay-specific results with the small event
count and overlapping forecast origins in mind; they are not calibrated
operational forecast estimates.

The refreshed run used 14 replay files (34,375 observations) and retained
30,913 labelled origins (199 positive). The validator excluded 1,774 candidate
origins: 718 with insufficient left-boundary history, 581 with missing
observations, and 475 with gappy lookback. The Eureka 2024+ test contains
6,050 origins (48 positive); the Wimberley 2019+ test contains 17,684 (73
positive); the cross-gauge test contains 29,588 (141 positive).

| Holdout | Model P / R / F1 | Trend baseline P / R / F1 | Model FP / FN |
|---|---:|---:|---:|
| Eureka 2024+ | 0.661 / 0.812 / 0.729 | 0.511 / 1.000 / 0.676 | 20 / 9 |
| Wimberley 2019+ | 0.008 / 0.452 / 0.015 | 0.760 / 0.521 / 0.618 | 4,355 / 40 |
| Cross-gauge 2019+ | 0.271 / 0.340 / 0.302 | 0.597 / 0.610 / 0.604 | 129 / 93 |

Eureka average precision is 0.777, ROC AUC 0.998 and Brier score 0.0045.
The Wimberley model raised alerts in both held-out 2024 controls. The
cross-gauge model underperformed the trend baseline and missed the Guadalupe
flood replay. Retain the Eureka-only scope; the small number of independent
flood events and overlapping origins do not support general flood forecasting
claims.

## Inference handoff

The repository also includes `riverguard/data`, a shared USGS data loader and
quality-reporting package. It normalizes supported readings to feet, preserves
missing values and source quality annotations, and does not interpolate gaps.
Its Eureka November 2024 fixture has two documented gaps; historical-window
reports cover the 2015–16 and 2017 Eureka replay spans. Those source checks
improve provenance and data handling, but do not establish model accuracy. The
`validate_model_input` is the shared serving contract. It accepts 15- or
30-minute cadence, filters future rows before value, quality, duplicate and gap
checks, requires `Approved` or legacy `A` approval, rejects nonempty
qualifiers, missing values and conflicting duplicates, and enforces the
six-hour left boundary plus its one-cadence lag sample. Maximum adjacent gap
and newest-reading age are each 1.5 times cadence. It returns machine-readable
quality metadata and named fallbacks. Replay training applies the same
validator rules using the replay's measured cadence. Keep evaluation
reproducible by training from committed replay files.

```python
from riverguard.data import load_stage
from ml.model import predict_risk, load_artifact

observations = load_stage("07019000", "2024-11-04", "2024-11-10")
model = load_artifact("backend/artifacts/ml/USGS-07019000")
result = predict_risk(
    observations, "2024-11-07T18:00:00Z", model=model, sample_interval_minutes=15
)
```

The data package is not yet wired into the backend container/API. That needs an
application integration change (include `riverguard/data` and its pandas
dependency in the image, then expose a route adapter); this ML branch keeps
that deployment wiring separate.

The saved Eureka artifact can be loaded with:

```python
from ml.model import load_artifact, predict_risk

model = load_artifact("backend/artifacts/ml/USGS-07019000")
result = predict_risk(
    observations, "2024-11-07T18:00:00Z", model=model, sample_interval_minutes=15
)
```

For another threshold, pass `flood_stage_ft` explicitly. Set
`sample_interval_minutes` to the verified Eureka cadence. `predict_risk()`
filters records after `as_of` before validating history and creating features.
If the model is absent or history is unusable, it returns a null probability
and named fallback. Keep this probability distinct from alert-policy thresholds.
