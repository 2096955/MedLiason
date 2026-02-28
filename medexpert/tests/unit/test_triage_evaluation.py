"""Unit tests for triage_evaluation tool — programmatic flag rules and emergency path."""

import pytest

# Test _apply_flag_rules
from lifesci_tools.triage_evaluation import _apply_flag_rules, _evaluate_emergency


class TestApplyFlagRules:
    """Tests for the programmatic flag override rules."""

    def test_low_mean_confidence_flags(self):
        """Mean confidence < 40 → flag for review."""
        flag, reason = _apply_flag_rules(
            flag=False, flag_reason=None,
            diagnosis="Tension Headache",
            mean_conf=35.0, eval_conf=70, dissenting=[],
        )
        assert flag is True
        assert "below 40%" in reason

    def test_high_confidence_dissenter_flags(self):
        """Dissenting specialist with confidence > 70 → flag."""
        dissenting = [{"specialist": "cardiologist", "alternative_diagnosis": "Angina", "confidence": 85}]
        flag, reason = _apply_flag_rules(
            flag=False, flag_reason=None,
            diagnosis="Acid Reflux",
            mean_conf=60.0, eval_conf=70, dissenting=dissenting,
        )
        assert flag is True
        assert "cardiologist" in reason
        assert "Angina" in reason

    def test_life_threatening_low_eval_flags(self):
        """Life-threatening diagnosis + eval_conf < 60 → flag."""
        flag, reason = _apply_flag_rules(
            flag=False, flag_reason=None,
            diagnosis="Myocardial Infarction",
            mean_conf=65.0, eval_conf=45, dissenting=[],
        )
        assert flag is True
        assert "life-threatening" in reason.lower() or "confidence" in reason.lower()

    def test_no_flag_when_all_good(self):
        """No flags when everything is fine."""
        flag, reason = _apply_flag_rules(
            flag=False, flag_reason=None,
            diagnosis="Common Cold",
            mean_conf=80.0, eval_conf=85, dissenting=[],
        )
        assert flag is False
        assert reason is None

    def test_existing_flag_preserved(self):
        """Pre-existing flag reason is preserved and combined with new reasons."""
        flag, reason = _apply_flag_rules(
            flag=True, flag_reason="LLM detected contradiction",
            diagnosis="Common Cold",
            mean_conf=35.0, eval_conf=85, dissenting=[],
        )
        assert flag is True
        assert "LLM detected contradiction" in reason
        assert "below 40%" in reason

    def test_dissenter_below_threshold_no_flag(self):
        """Dissenting specialist with confidence <= 70 → no additional flag."""
        dissenting = [{"specialist": "neurologist", "alternative_diagnosis": "Migraine", "confidence": 50}]
        flag, reason = _apply_flag_rules(
            flag=False, flag_reason=None,
            diagnosis="Tension Headache",
            mean_conf=70.0, eval_conf=75, dissenting=dissenting,
        )
        assert flag is False
        assert reason is None

    def test_multiple_rules_combine_reasons(self):
        """Multiple flag rules triggered → reasons are joined with semicolons."""
        dissenting = [{"specialist": "cardiologist", "alternative_diagnosis": "MI", "confidence": 90}]
        flag, reason = _apply_flag_rules(
            flag=False, flag_reason=None,
            diagnosis="Sepsis",
            mean_conf=30.0, eval_conf=40, dissenting=dissenting,
        )
        assert flag is True
        # Should have at least 2 reasons (low mean conf + high dissenter + life-threatening)
        assert reason.count(";") >= 1


class TestEvaluateEmergency:
    """Tests for the emergency fast-path evaluation."""

    def test_emergency_confirmed_with_matching_keywords(self):
        """Emergency confirmed when clinical note contains emergency keywords."""
        result = _evaluate_emergency(
            "Emergency presentation",
            '{"chief_complaint": "chest pain and difficulty breathing"}',
        )
        assert result["confirmed_emergency"] is True
        assert result["eval_confidence"] == 95
        assert result["flag_for_review"] is False

    def test_emergency_not_confirmed_without_keywords(self):
        """Emergency flagged but not confirmed when symptoms don't match keywords."""
        result = _evaluate_emergency(
            "Emergency presentation",
            '{"chief_complaint": "mild headache"}',
        )
        assert result["confirmed_emergency"] is False
        assert result["eval_confidence"] == 60
        assert result["flag_for_review"] is True
        assert "could not be confirmed" in result["flag_reason"]

    def test_emergency_uses_diagnosis_fallback(self):
        """Empty diagnosis defaults to 'Emergency presentation'."""
        result = _evaluate_emergency("", '{"chief_complaint": "seizure activity"}')
        assert result["diagnosis"] == "Emergency presentation"

    def test_emergency_handles_non_string_note(self):
        """Non-string clinical note doesn't crash."""
        result = _evaluate_emergency("Emergency", 12345)
        assert result["confirmed_emergency"] is False
        assert result["flag_for_review"] is True
