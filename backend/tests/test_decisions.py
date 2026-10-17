from datetime import datetime, timedelta, timezone
import unittest

from app.decisions import AlertPolicy, DecisionState, decide_alert


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


if __name__ == "__main__":
    unittest.main()
