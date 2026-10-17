import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from sklearn.dummy import DummyClassifier

from backend.ml.model import (
    FEATURE_COLUMNS,
    FittedRiskModel,
    build_examples,
    load_artifact,
    predict_risk,
    save_artifact,
)
from backend.ml.validation import validate_model_input


AS_OF = pd.Timestamp("2026-01-01T12:00:00Z")


def series(*, periods=26, freq="15min", stage=3.0):
    times = pd.date_range(end=AS_OF, periods=periods, freq=freq)
    return pd.DataFrame({
        "timestamp": times,
        "stage_ft": stage,
        "approval_status": "Approved",
        "qualifier": None,
    })


class ValidationTests(unittest.TestCase):
    def test_future_rows_cannot_change_past_validation_or_prediction(self):
        base = series()
        extra = pd.DataFrame({
            "timestamp": [AS_OF + pd.Timedelta(minutes=15)],
            "stage_ft": [np.nan],
            "approval_status": ["Provisional"],
            "qualifier": ["ice"],
        })
        valid = validate_model_input(base, as_of=AS_OF, sample_interval_minutes=15)
        with_future = validate_model_input(pd.concat([base, extra]), as_of=AS_OF, sample_interval_minutes=15)
        self.assertTrue(valid.usable)
        assert_frame_equal(valid.observations.reset_index(drop=True), with_future.observations.reset_index(drop=True))
        self.assertEqual(valid.fallback, with_future.fallback)
        self.assertEqual(len(valid.observations), len(with_future.observations))

    def test_six_hour_boundary_and_newest_age_limits_are_inclusive(self):
        exact = series()
        self.assertTrue(validate_model_input(exact, as_of=AS_OF, sample_interval_minutes=15).usable)
        at_limit = series(periods=27)
        self.assertTrue(validate_model_input(
            at_limit,
            as_of=AS_OF + pd.Timedelta(minutes=22, seconds=30),
            sample_interval_minutes=15,
        ).usable)
        older = series(periods=27)
        older = older.iloc[:-1].copy()
        older.loc[older.index[-1], "timestamp"] = AS_OF - pd.Timedelta(minutes=22, seconds=31)
        self.assertEqual(validate_model_input(older, as_of=AS_OF, sample_interval_minutes=15).fallback, "stale_history")

    def test_a_later_reading_cannot_substitute_for_the_left_boundary_lag(self):
        too_late = series(periods=25)  # Starts exactly at the six-hour boundary.
        self.assertEqual(
            validate_model_input(too_late.iloc[1:], as_of=AS_OF, sample_interval_minutes=15).fallback,
            "insufficient_history",
        )

    def test_missing_values_and_gaps_are_rejected(self):
        missing = series()
        missing.loc[10, "stage_ft"] = np.nan
        self.assertEqual(validate_model_input(missing, as_of=AS_OF, sample_interval_minutes=15).fallback, "missing_observations")
        gappy = series().drop(index=10)
        self.assertEqual(validate_model_input(gappy, as_of=AS_OF, sample_interval_minutes=15).fallback, "gappy_history")

    def test_quality_and_duplicate_contract(self):
        unapproved = series()
        unapproved.loc[4, "approval_status"] = "Provisional"
        self.assertEqual(validate_model_input(unapproved, as_of=AS_OF, sample_interval_minutes=15).fallback, "quality_not_approved")
        qualified = series()
        qualified.loc[4, "qualifier"] = "Ice"
        self.assertEqual(validate_model_input(qualified, as_of=AS_OF, sample_interval_minutes=15).fallback, "unsupported_qualifier")
        duplicate = series()
        duplicate.loc[len(duplicate)] = duplicate.iloc[-1]
        self.assertTrue(validate_model_input(duplicate, as_of=AS_OF, sample_interval_minutes=15).usable)
        duplicate.loc[len(duplicate)] = duplicate.iloc[-1].copy()
        duplicate.loc[duplicate.index[-1], "stage_ft"] = 8.0
        self.assertEqual(validate_model_input(duplicate, as_of=AS_OF, sample_interval_minutes=15).fallback, "conflicting_duplicates")

    def test_model_fallbacks_and_artifact_roundtrip(self):
        self.assertEqual(predict_risk(series(stage=20), AS_OF.isoformat(), sample_interval_minutes=15)["fallback"], "already_above_threshold_ongoing_flood")
        self.assertEqual(predict_risk(series(), AS_OF.isoformat(), sample_interval_minutes=15)["fallback"], "validated_model_not_loaded")
        estimator = DummyClassifier(strategy="prior").fit(pd.DataFrame([[0] * len(FEATURE_COLUMNS), [1] * len(FEATURE_COLUMNS)], columns=FEATURE_COLUMNS), [0, 1])
        model = FittedRiskModel(estimator)
        with tempfile.TemporaryDirectory() as directory:
            save_artifact(model, {}, directory, training_data_id="test", train_end="2025-01-01T00:00:00Z")
            self.assertIsInstance(load_artifact(directory), FittedRiskModel)

    def test_training_does_not_clean_away_lookback_gaps(self):
        observations = series(periods=70)
        observations = observations.drop(index=30)
        examples = build_examples(observations, cadence_minutes=15)
        self.assertGreater(examples.attrs.get("excluded_windows", {}).get("gappy_history", 0), 0)


if __name__ == "__main__":
    unittest.main()
