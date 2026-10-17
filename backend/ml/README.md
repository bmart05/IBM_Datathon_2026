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

From `backend/ml/` in a Python environment with scikit-learn:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python train_replays.py
```

The script prints exact metrics, source file checksums, label counts and
per-replay results. It writes the Eureka estimator and metadata to
`backend/artifacts/ml/USGS-07019000/`; artifacts are ignored by Git. Regenerate
the model inside LinuxONE with its installed package versions before serving
it there.

## Evaluation results

The current run uses all 14 replay files: **34,375 observations** from three
stations, labelled into **31,130 forecast origins** (199 positive, 30,931
negative). The two new Guadalupe control windows and July 2025 flood replay
come from the updated backend branch. The July event uses the NOAA minor-flood
threshold of 9 ft at USGS 08166200.
The positive windows are highly concentrated around flood events; adjacent
forecast origins overlap and are not independent samples.

| Evaluation | Method | Precision | Recall | F1 | False positive windows | Missed positive windows |
|---|---|---:|---:|---:|---:|---:|
| Eureka, 2024+ holdout | Threshold-normalized HistGradientBoosting | 0.661 | 0.813 | 0.729 | 20 | 9 |
| Eureka, 2024+ holdout | Trend extrapolation | 0.511 | 1.000 | 0.676 | 46 | 0 |
| Wimberley, 2019+ holdout | Station-specific HistGradientBoosting | 0.007 | 0.452 | 0.015 | 4,415 | 40 |
| Wimberley, 2019+ holdout | Trend extrapolation | 0.760 | 0.521 | 0.618 | 12 | 35 |
| All stations, 2019+ holdout | Cross-gauge HistGradientBoosting | 0.302 | 0.277 | 0.289 | 90 | 102 |
| All stations, 2019+ holdout | Trend extrapolation | 0.597 | 0.610 | 0.604 | 58 | 55 |

Eureka's held-out model detected both flood replay events and raised no alerts
in either control replay. Its average precision was 0.777, ROC AUC 0.998, and
Brier score 0.0044. The Wimberley-only model raised alerts in both held-out
control months, so it is **not suitable for deployment**. In the cross-gauge
report, the combined model detected the held-out Eureka events and the 2019
Wimberley event, but missed all 20 positive windows in the July 2025 Guadalupe
flood replay. It underperformed the trend baseline overall. Relative-stage
features alone do not make one river's model transfer reliably to another.

These are replay-specific estimates, not calibrated operational forecasts.
There are only two held-out Eureka floods, one Wimberley flood and one
Guadalupe flood;
row-level metrics overstate evidence because many origins describe the same
event. The result supports a Eureka-only prototype, with human review and the
trend baseline retained as a comparator. It does not support claiming
generalization across gauges.

## Inference handoff

The repository also includes `riverguard/data`, a shared USGS data loader and
quality-reporting package. It normalizes supported readings to feet, preserves
missing values and source quality annotations, and does not interpolate gaps.
Its Eureka November 2024 fixture has two documented gaps; historical-window
reports cover the 2015–16 and 2017 Eureka replay spans. Those source checks
improve provenance and data handling, but do not establish model accuracy. The
loader's `timestamp` and `stage_ft` columns can be passed directly to this
model. Keep replay evaluation reproducible by training from committed replay
files; do not silently refresh retrospective source data during evaluation.

```python
from riverguard.data import load_stage, observations_as_of
from model import predict_risk, load_artifact

observations = load_stage("07019000", "2024-11-04", "2024-11-10")
observations = observations_as_of(observations, "2024-11-07T18:00:00Z")
model = load_artifact("backend/artifacts/ml/USGS-07019000")
result = predict_risk(observations, "2024-11-07T18:00:00Z", model=model)
```

The data package is not yet wired into the backend container/API. That needs an
application integration change (include `riverguard/data` and its pandas
dependency in the image, then expose a route adapter); this ML branch keeps
that deployment wiring separate.

The saved Eureka artifact can be loaded with:

```python
from model import load_artifact, predict_risk

model = load_artifact("backend/artifacts/ml/USGS-07019000")
result = predict_risk(observations, "2024-11-07T18:00:00Z", model=model)
```

For another threshold, pass `flood_stage_ft` explicitly. `predict_risk()`
filters records after `as_of` before creating features. If the validated model
is absent or lacks enough recent history, it returns a null probability and a
named fallback instead of inventing an estimate. The backend should expose the
model version and keep this probability distinct from alert-policy thresholds.
