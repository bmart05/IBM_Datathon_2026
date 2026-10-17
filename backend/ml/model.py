"""Leakage-conscious six-hour flood threshold prediction.

Input observations follow the shared contract: ``timestamp`` (UTC) and
``stage_ft`` (numeric). The model predicts a *new* upward crossing of the
flood threshold; records already above the threshold are not positive examples.

No fitted model is bundled: train on the team's verified USGS replay data and
persist the resulting estimator in the backend's controlled artifacts area.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

MODEL_NAME = "hist_gradient_boosting"
MODEL_VERSION = "v2"
DEFAULT_FLOOD_STAGE_FT = 19.0
FEATURE_COLUMNS = (
    "stage_ratio",
    "change_1h_ratio",
    "change_3h_ratio",
    "change_6h_ratio",
    "mean_3h_ratio",
    "std_3h_ratio",
    "rate_1h_ratio_per_hour",
)


def _clean_observations(observations: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "stage_ft"}
    missing = required.difference(observations.columns)
    if missing:
        raise ValueError(f"observations missing required columns: {sorted(missing)}")
    frame = observations.loc[:, ["timestamp", "stage_ft"]].copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["stage_ft"] = pd.to_numeric(frame["stage_ft"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "stage_ft"])
    # Pandas 3 may parse ISO strings at microsecond resolution. Normalize units
    # because the vectorized window arithmetic below uses nanosecond durations.
    frame["timestamp"] = frame["timestamp"].dt.as_unit("ns")
    # Duplicate timestamps are ambiguous; keep the last source row deterministically.
    return frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _features_at(
    history: pd.DataFrame, at: pd.Timestamp, flood_stage_ft: float
) -> dict[str, float] | None:
    """Create features from readings at/before ``at`` only.

    Stage changes use the closest observation at or before the requested lag.
    A tolerance prevents stale readings from silently becoming current data.
    """
    times_ns = history.timestamp.astype("int64").to_numpy()
    stages = history.stage_ft.to_numpy(dtype=float)
    at_ns = at.value
    end = int(np.searchsorted(times_ns, at_ns, side="right"))
    if end == 0 or at_ns - times_ns[end - 1] > pd.Timedelta(hours=1).value:
        return None
    return _features_from_arrays(times_ns, stages, end, at_ns, flood_stage_ft)


def _features_from_arrays(
    times_ns: np.ndarray,
    stages: np.ndarray,
    end: int,
    at_ns: int,
    flood_stage_ft: float,
) -> dict[str, float] | None:
    """Array implementation shared by batch labelling and online inference."""
    current = float(stages[end - 1])

    def lag_stage(hours: int) -> float | None:
        lag_ns = at_ns - pd.Timedelta(hours=hours).value
        index = int(np.searchsorted(times_ns[:end], lag_ns, side="right")) - 1
        if index < 0:
            return None
        if at_ns - times_ns[index] > pd.Timedelta(hours=hours + 1).value:
            return None
        return float(stages[index])

    one, three, six = (lag_stage(h) for h in (1, 3, 6))
    if one is None or three is None or six is None:
        return None
    window_start = int(np.searchsorted(
        times_ns[:end], at_ns - pd.Timedelta(hours=3).value, side="right"
    ))
    window = stages[window_start:end]
    if not np.isfinite(flood_stage_ft) or flood_stage_ft <= 0:
        raise ValueError("flood_stage_ft must be a positive finite number")
    return {
        "stage_ratio": current / flood_stage_ft,
        "change_1h_ratio": (current - one) / flood_stage_ft,
        "change_3h_ratio": (current - three) / flood_stage_ft,
        "change_6h_ratio": (current - six) / flood_stage_ft,
        "mean_3h_ratio": float(window.mean()) / flood_stage_ft if len(window) else current / flood_stage_ft,
        "std_3h_ratio": float(window.std(ddof=0)) / flood_stage_ft if len(window) else 0.0,
        "rate_1h_ratio_per_hour": (current - one) / flood_stage_ft,
    }


def build_examples(
    observations: pd.DataFrame,
    flood_stage_ft: float = DEFAULT_FLOOD_STAGE_FT,
    horizon_hours: int = 6,
    cadence_minutes: int = 15,
) -> pd.DataFrame:
    """Build supervised examples without looking beyond each prediction time.

    Labels are 1 only when a below-threshold observation is followed by an
    upward crossing within the horizon. Windows with a data gap greater than
    1.5x the expected cadence are excluded. Events already above threshold
    are excluded as ongoing floods, not relabelled as new crossings.
    """
    if horizon_hours <= 0 or cadence_minutes <= 0:
        raise ValueError("horizon_hours and cadence_minutes must be positive")
    frame = _clean_observations(observations)
    if len(frame) < 3:
        return pd.DataFrame(columns=["timestamp", "threshold_ft", *FEATURE_COLUMNS, "label"])
    times = frame.timestamp
    times_ns = times.astype("int64").to_numpy()
    stages = frame.stage_ft.to_numpy(dtype=float)
    records: list[dict[str, Any]] = []
    gap_limit_ns = pd.Timedelta(minutes=cadence_minutes * 1.5).value
    horizon_ns = pd.Timedelta(hours=horizon_hours).value

    for i, at in enumerate(times):
        stage = stages[i]
        if stage >= flood_stage_ft:
            continue
        # Require complete forward observation through the horizon.
        at_ns = times_ns[i]
        end_ns = at_ns + horizon_ns
        future_end = int(np.searchsorted(times_ns, end_ns, side="right"))
        if future_end <= i + 1 or times_ns[future_end - 1] < end_ns - gap_limit_ns:
            continue
        if np.diff(times_ns[i:future_end]).max(initial=0) > gap_limit_ns:
            continue
        features = _features_from_arrays(times_ns, stages, i + 1, at_ns, flood_stage_ft)
        if features is None:
            continue
        label = int(bool((stages[i + 1:future_end] >= flood_stage_ft).any()))
        records.append({"timestamp": at, "threshold_ft": flood_stage_ft, **features, "label": label})
    return pd.DataFrame.from_records(records, columns=["timestamp", "threshold_ft", *FEATURE_COLUMNS, "label"])


@dataclass
class FittedRiskModel:
    estimator: Any
    flood_stage_ft: float | None = DEFAULT_FLOOD_STAGE_FT
    horizon_hours: int = 6
    model_name: str = MODEL_NAME
    model_version: str = MODEL_VERSION


def train_model(examples: pd.DataFrame, random_state: int = 42) -> FittedRiskModel:
    """Fit the recommended regularised tree model on caller-selected train rows."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    usable = examples.dropna(subset=[*FEATURE_COLUMNS, "label"])
    if usable.label.nunique() < 2:
        raise ValueError("training data must contain both crossing and non-crossing labels")
    estimator = HistGradientBoostingClassifier(
        max_iter=100,
        learning_rate=0.08,
        max_leaf_nodes=7,
        l2_regularization=1.0,
        random_state=random_state,
    )
    estimator.fit(usable.loc[:, FEATURE_COLUMNS], usable.label.astype(int))
    thresholds = (
        usable["threshold_ft"].dropna().astype(float).unique()
        if "threshold_ft" in usable
        else np.array([], dtype=float)
    )
    fitted_threshold = float(thresholds[0]) if len(thresholds) == 1 else None
    return FittedRiskModel(estimator=estimator, flood_stage_ft=fitted_threshold)


def _probability(model: FittedRiskModel, features: dict[str, float]) -> float:
    return float(model.estimator.predict_proba(pd.DataFrame([features], columns=FEATURE_COLUMNS))[0, 1])


def predict_risk(
    observations: pd.DataFrame,
    as_of: str,
    horizon_hours: int = 6,
    model: FittedRiskModel | None = None,
    flood_stage_ft: float = DEFAULT_FLOOD_STAGE_FT,
) -> dict[str, Any]:
    """Predict crossing risk using only rows at or before ``as_of``.

    If the caller supplies no fitted and evaluated model, returns a null
    probability with an explicit persistence fallback; it never invents one.
    """
    cutoff = pd.to_datetime(as_of, utc=True, errors="raise")
    history = _clean_observations(observations)
    history = history.loc[history.timestamp <= cutoff].reset_index(drop=True)
    result: dict[str, Any] = {
        "crossing_probability": None,
        "horizon_hours": horizon_hours,
        "model_name": MODEL_NAME if model else "persistence_fallback",
        "model_version": MODEL_VERSION if model else None,
        "fallback": None,
    }
    if horizon_hours <= 0:
        raise ValueError("horizon_hours must be positive")
    if history.empty:
        result["fallback"] = "no_observations_at_or_before_as_of"
        return result
    current = float(history.stage_ft.iloc[-1])
    result["current_stage_ft"] = current
    if current >= flood_stage_ft:
        result["fallback"] = "already_above_threshold_ongoing_flood"
        result["model_name"] = "ongoing_flood_state"
        return result
    features = _features_at(history, cutoff, flood_stage_ft)
    if features is None:
        result["fallback"] = "insufficient_recent_history_for_features"
        return result
    if model is None:
        result["fallback"] = "validated_model_not_loaded"
        return result
    if horizon_hours != model.horizon_hours or (
        model.flood_stage_ft is not None and flood_stage_ft != model.flood_stage_ft
    ):
        result["fallback"] = "model_configuration_mismatch"
        return result
    result["crossing_probability"] = _probability(model, features)
    result["model_name"] = model.model_name
    result["model_version"] = model.model_version
    result["fallback"] = None
    return result


def evaluate_temporally(
    examples: pd.DataFrame,
    *,
    train_end: str,
    probability_threshold: float = 0.5,
    flood_stage_ft: float = DEFAULT_FLOOD_STAGE_FT,
    horizon_hours: int = 6,
    group_column: str | None = None,
) -> dict[str, Any]:
    """Train before a fixed UTC cutoff and compare only on later observations."""
    from sklearn.metrics import average_precision_score, brier_score_loss, confusion_matrix, roc_auc_score

    data = examples.dropna(subset=[*FEATURE_COLUMNS, "label"]).copy()
    data["timestamp"] = pd.to_datetime(data.timestamp, utc=True)
    cutoff = pd.to_datetime(train_end, utc=True)
    # Purge training labels whose six-hour outcome window overlaps the test
    # period. Otherwise the same flood event can leak across the time split.
    purge = pd.Timedelta(hours=horizon_hours)
    train = data.loc[data.timestamp < cutoff - purge]
    test = data.loc[data.timestamp >= cutoff]
    if train.label.nunique() < 2:
        raise ValueError("pre-cutoff training window must contain both classes")
    if test.empty or test.label.nunique() < 2:
        raise ValueError("post-cutoff test window must contain both classes for meaningful evaluation")
    model = train_model(train)
    probs = model.estimator.predict_proba(test.loc[:, FEATURE_COLUMNS])[:, 1]
    y = test.label.astype(int).to_numpy()
    pred = (probs >= probability_threshold).astype(int)
    # Persistence predicts a crossing only if already at threshold; these rows
    # are excluded from examples, so its crossing forecast is always negative.
    persistence = np.zeros_like(y)
    # Extrapolate the observed six-hour relative-stage change over the next six.
    trend_stage_ratio = test.stage_ratio.to_numpy() + test.change_6h_ratio.to_numpy()
    trend = (trend_stage_ratio >= 1.0).astype(int)

    def scores(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
        tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        return {
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(2 * precision * recall / (precision + recall)) if precision + recall else 0.0,
            "false_positives": int(fp),
            "false_negatives": int(fn),
            "true_positives": int(tp),
            "true_negatives": int(tn),
        }

    group_metrics = None
    if group_column and group_column in test.columns:
        group_metrics = {}
        evaluated = test.assign(actual=y, predicted=pred)
        for group_name, group in evaluated.groupby(group_column, sort=True):
            actual_positive = int(group.actual.sum())
            predicted_positive = int(group.predicted.sum())
            tp = int(((group.actual == 1) & (group.predicted == 1)).sum())
            group_metrics[str(group_name)] = {
                "origins": int(len(group)),
                "positive_windows": actual_positive,
                "predicted_alert_windows": predicted_positive,
                "true_positive_windows": tp,
                "missed_positive_windows": actual_positive - tp,
                "false_alert_windows": predicted_positive - tp,
                "event_detected": bool(actual_positive > 0 and tp > 0),
                "false_alert_replay": bool(actual_positive == 0 and predicted_positive > 0),
                "dataset_role": str(group.dataset_role.iloc[0]) if "dataset_role" in group else None,
                "station_id": str(group.station_id.iloc[0]) if "station_id" in group else None,
                "threshold_ft": float(group.threshold_ft.iloc[0]) if "threshold_ft" in group else None,
            }

    thresholds = (
        sorted(float(value) for value in data.threshold_ft.dropna().unique())
        if "threshold_ft" in data
        else [flood_stage_ft]
    )
    return {
        "model": {
            **scores(y, pred),
            "average_precision": float(average_precision_score(y, probs)),
            "roc_auc": float(roc_auc_score(y, probs)),
            "brier_score": float(brier_score_loss(y, probs)),
        },
        "persistence_baseline": scores(y, persistence),
        "trend_extrapolation_baseline": scores(y, trend),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "positive_test_rows": int(y.sum()),
        "train_end_exclusive": (cutoff - purge).isoformat(),
        "label_purge_hours": horizon_hours,
        "test_start_inclusive": test.timestamp.min().isoformat(),
        "flood_stages_ft": thresholds,
        "horizon_hours": horizon_hours,
        "probability_threshold": probability_threshold,
        "replay_groups": group_metrics,
        "limitations": [
            "Metrics are only meaningful if the test period contains independent flood events.",
            "Adjacent windows overlap; report event-level performance and warning lead time as well.",
            "Do not describe these estimates as general flood forecasting accuracy.",
        ],
    }


def save_artifact(
    model: FittedRiskModel,
    evaluation: dict[str, Any],
    directory: str | Path,
    *,
    training_data_id: str,
    train_end: str,
) -> dict[str, str]:
    """Persist estimator and auditable metadata after evaluation is complete."""
    import joblib

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    model_path = target / "riverguard_model.joblib"
    metadata_path = target / "model_metadata.json"
    joblib.dump(model, model_path)
    metadata = {
        "model_name": model.model_name,
        "model_version": model.model_version,
        "flood_stage_ft": model.flood_stage_ft,
        "horizon_hours": model.horizon_hours,
        "training_data_id": training_data_id,
        "train_end": pd.to_datetime(train_end, utc=True).isoformat(),
        "evaluation": evaluation,
        "feature_columns": list(FEATURE_COLUMNS),
        "python_package": "scikit-learn HistGradientBoostingClassifier",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"model_path": str(model_path), "metadata_path": str(metadata_path)}


def load_artifact(directory: str | Path) -> FittedRiskModel:
    """Load the locally generated estimator artifact."""
    import joblib

    return joblib.load(Path(directory) / "riverguard_model.joblib")
