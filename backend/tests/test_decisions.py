from datetime import datetime, timedelta, timezone
import unittest

from app.decisions import AlertPolicy, DecisionState, POLICY_THRESHOLDS, decide_alert, decide_crossing_alert


NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


def decide(**overrides):
    values = {
        "policy": AlertPolicy.balanced,
        "probability_exceedance": 0.7,
        "latest_stage_m": 4.0,
        "forecast_peak_m": 4.2,
        "flood_stage_m": 5.0,
        "observed_at": NOW,
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return decide_alert(**values)


class DecisionScenarios(unittest.TestCase):
    def test_observed_flood_threshold_is_policy_independent(self):
        for policy in AlertPolicy:
            with self.subTest(policy=policy):
                result = decide(policy=policy, latest_stage_m=5.0)
                self.assertIs(result.state, DecisionState.threshold_exceeded)
                self.assertIn("observed river stage", result.explanation.lower())

    def test_observed_crossing_takes_precedence_when_model_output_is_missing(self):
        result = decide(latest_stage_m=5.1, probability_exceedance=None, forecast_peak_m=None)
        self.assertIs(result.state, DecisionState.threshold_exceeded)

    def test_early_policy_escalates_forecast_risk_before_balanced(self):
        early = decide(policy=AlertPolicy.early, probability_exceedance=0.4, forecast_peak_m=5.1)
        balanced = decide(policy=AlertPolicy.balanced, probability_exceedance=0.4, forecast_peak_m=5.1)
        self.assertIs(early.state, DecisionState.review)
        self.assertIs(balanced.state, DecisionState.watch)

    def test_rising_near_threshold_is_watch_and_explains_why(self):
        result = decide(probability_exceedance=0.2, forecast_peak_m=4.6)
        self.assertIs(result.state, DecisionState.watch)
        self.assertIn("within 10%", result.explanation)

    def test_falling_below_threshold_is_normal(self):
        result = decide(probability_exceedance=0.1, latest_stage_m=4.5, forecast_peak_m=4.2)
        self.assertIs(result.state, DecisionState.normal)

    def test_missing_stale_or_future_timestamp_fails_closed(self):
        for observed_at in (None, NOW - timedelta(hours=3), NOW + timedelta(minutes=1)):
            with self.subTest(observed_at=observed_at):
                result = decide(observed_at=observed_at)
                self.assertIs(result.state, DecisionState.data_unavailable)
                self.assertIsNone(result.policy_threshold)

    def test_missing_forecast_fails_closed(self):
        self.assertIs(decide(forecast_peak_m=None).state, DecisionState.data_unavailable)

    def test_same_inputs_and_policy_produce_same_decision(self):
        self.assertEqual(decide(), decide())


def decide_crossing(**overrides):
    values = {
        "policy": AlertPolicy.balanced,
        "crossing_probability": 0.6,
        "current_stage_ft": 18.0,
        "flood_stage_ft": 19.0,
        "observed_at": NOW,
        "as_of": NOW,
        "data_quality_valid": True,
        "sample_interval_minutes": 15,
    }
    values.update(overrides)
    return decide_crossing_alert(**values)


class CrossingDecisionScenarios(unittest.TestCase):
    def test_each_policy_cutoff_is_inclusive_and_just_below_is_normal(self):
        for policy, cutoff in POLICY_THRESHOLDS.items():
            with self.subTest(policy=policy):
                self.assertIs(decide_crossing(policy=policy, crossing_probability=cutoff).state, DecisionState.review)
                below = decide_crossing(policy=policy, crossing_probability=cutoff - 0.001)
                self.assertIs(below.state, DecisionState.normal)
                self.assertIn("uncalibrated", below.explanation)

    def test_observed_crossing_precedes_null_score_but_requires_valid_fresh_observation(self):
        result = decide_crossing(crossing_probability=None, current_stage_ft=19.0, fallback="already_above_threshold_ongoing_flood")
        self.assertIs(result.state, DecisionState.threshold_exceeded)
        self.assertIn("Observed", result.explanation)
        # A gappy feature history prevents model scoring, but does not erase a
        # separate, fresh and source-valid measurement of an observed crossing.
        gappy = decide_crossing(crossing_probability=None, current_stage_ft=19.0, fallback="gappy_history")
        self.assertIs(gappy.state, DecisionState.threshold_exceeded)
        self.assertIs(decide_crossing(crossing_probability=None, current_stage_ft=19.0, data_quality_valid=False).state, DecisionState.data_unavailable)

    def test_null_below_threshold_score_preserves_named_fallback(self):
        result = decide_crossing(crossing_probability=None, fallback="validated_model_not_loaded")
        self.assertIs(result.state, DecisionState.data_unavailable)
        self.assertIn("validated_model_not_loaded", result.explanation)

    def test_missing_timestamp_stale_future_and_invalid_quality_fail_closed(self):
        cases = [
            {"observed_at": None},
            {"observed_at": NOW - timedelta(minutes=23)},
            {"observed_at": NOW + timedelta(seconds=1)},
            {"data_quality_valid": False, "fallback": "quality_not_approved"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.assertIs(decide_crossing(**kwargs).state, DecisionState.data_unavailable)

    def test_freshness_boundary_is_accepted_for_each_supported_cadence(self):
        for cadence in (15, 30):
            with self.subTest(cadence=cadence):
                exact_limit = NOW - timedelta(minutes=cadence * 1.5)
                accepted = decide_crossing(observed_at=exact_limit, sample_interval_minutes=cadence)
                self.assertIs(accepted.state, DecisionState.review)
                stale = decide_crossing(observed_at=exact_limit - timedelta(microseconds=1), sample_interval_minutes=cadence)
                self.assertIs(stale.state, DecisionState.data_unavailable)

    def test_historical_as_of_is_deterministic_and_no_peak_is_required(self):
        result = decide_crossing(crossing_probability=0.9, as_of=NOW)
        self.assertIs(result.state, DecisionState.review)
        self.assertEqual(result, decide_crossing(crossing_probability=0.9, as_of=NOW))
        self.assertIn("not a predicted peak", result.explanation.lower())

    def test_invalid_score_fails_closed(self):
        for score in (-0.01, 1.01, float("nan")):
            with self.subTest(score=score):
                self.assertIs(decide_crossing(crossing_probability=score).state, DecisionState.data_unavailable)


if __name__ == "__main__":
    unittest.main()
