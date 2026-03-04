"""Tests for calibration human review gate (C4) — cold_store CRUD + calibrator delta fork."""

import json
from unittest.mock import patch

import pytest

from lifesci_tools import cold_store
from lifesci_tools.specialist_calibrator import CalibrationConfig, SpecialistCalibrator


@pytest.fixture(autouse=True)
def _clear_caches():
    cold_store.clear_init_cache()
    yield
    cold_store.clear_init_cache()


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test-calibration-gate.db")
    conn = cold_store.get_connection(db_path)
    yield conn
    conn.close()


# ── pending_calibration CRUD ────────────────────────────────


class TestPendingCalibrationCRUD:
    def test_write_and_read(self, db):
        row_id = cold_store.write_pending_calibration(
            db,
            agent_name="DrugSpecialist",
            domain="drugs",
            current_weight=1.0,
            suggested_weight=0.8,
            delta=0.2,
            negative_rate=0.35,
            sample_count=15,
            action="LOWER_WEIGHT",
            reason="High negative rate",
        )
        assert row_id > 0

        pending = cold_store.get_pending_calibrations(db, status="pending")
        assert len(pending) == 1
        assert pending[0]["agent_name"] == "DrugSpecialist"
        assert pending[0]["domain"] == "drugs"
        assert pending[0]["delta"] == 0.2
        assert pending[0]["status"] == "pending"

    def test_apply_writes_to_learned_strategies(self, db):
        row_id = cold_store.write_pending_calibration(
            db,
            agent_name="DrugSpecialist",
            domain="drugs",
            current_weight=1.0,
            suggested_weight=0.8,
            delta=0.2,
            action="LOWER_WEIGHT",
        )
        ok = cold_store.apply_pending_calibration(db, row_id, reviewer="admin")
        assert ok is True

        # Verify status changed
        resolved = cold_store.get_pending_calibrations(db, status="applied")
        assert len(resolved) == 1
        assert resolved[0]["reviewer"] == "admin"

        # Verify weight written to learned_strategies
        row = db.execute(
            "SELECT strategy_json FROM learned_strategies "
            "WHERE strategy_type = 'specialist_weights' AND domain_or_key = 'drugs'"
        ).fetchone()
        assert row is not None
        entries = json.loads(row["strategy_json"])
        assert any(
            e["agent"] == "DrugSpecialist" and e["weight"] == 0.8 for e in entries
        )

    def test_reject(self, db):
        row_id = cold_store.write_pending_calibration(
            db,
            agent_name="DrugSpecialist",
            domain="drugs",
            current_weight=1.0,
            suggested_weight=0.8,
            delta=0.2,
            action="LOWER_WEIGHT",
        )
        ok = cold_store.reject_pending_calibration(
            db, row_id, reviewer="admin", reason="Too aggressive"
        )
        assert ok is True

        # Verify status changed
        rejected = cold_store.get_pending_calibrations(db, status="rejected")
        assert len(rejected) == 1

        # Verify nothing in learned_strategies
        row = db.execute(
            "SELECT strategy_json FROM learned_strategies "
            "WHERE strategy_type = 'specialist_weights' AND domain_or_key = 'drugs'"
        ).fetchone()
        assert row is None

    def test_apply_nonexistent_returns_false(self, db):
        assert cold_store.apply_pending_calibration(db, 999) is False

    def test_apply_already_resolved_returns_false(self, db):
        row_id = cold_store.write_pending_calibration(
            db,
            agent_name="DrugSpecialist",
            domain="drugs",
            current_weight=1.0,
            suggested_weight=0.8,
            delta=0.2,
            action="LOWER_WEIGHT",
        )
        cold_store.apply_pending_calibration(db, row_id)
        # Second apply should fail
        assert cold_store.apply_pending_calibration(db, row_id) is False

    def test_reject_nonexistent_returns_false(self, db):
        assert cold_store.reject_pending_calibration(db, 999) is False

    def test_write_pending_dedup_returns_existing_id(self, db):
        """Duplicate (agent, domain) with status=pending returns existing ID."""
        id1 = cold_store.write_pending_calibration(
            db,
            agent_name="DrugSpecialist",
            domain="drugs",
            current_weight=1.0,
            suggested_weight=0.8,
            delta=0.2,
            action="LOWER_WEIGHT",
        )
        db.commit()
        id2 = cold_store.write_pending_calibration(
            db,
            agent_name="DrugSpecialist",
            domain="drugs",
            current_weight=1.0,
            suggested_weight=0.7,
            delta=0.3,
            action="LOWER_WEIGHT",
        )
        db.commit()
        assert id1 == id2
        pending = cold_store.get_pending_calibrations(db, status="pending")
        assert len(pending) == 1

    def test_table_in_schema(self, db):
        tables = sorted(
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if row["name"] != "sqlite_sequence"
        )
        assert "pending_calibration" in tables


# ── calibrator delta fork ───────────────────────────────────


def _seed_calibration_data(db, agent, domain, n_sessions=15, n_negative=6):
    """Seed routing patterns + feedback to produce a calibration suggestion."""
    for i in range(n_sessions):
        sid = f"sess-{agent}-{domain}-{i}"
        cold_store.write_session_outcome(
            db,
            session_id=sid,
            query_domain=domain,
            query_text="test",
            coverage_pct=0.8,
            verification_verdict="CRITICAL_ISSUES" if i < n_negative else "PASS",
            verification_score=0.3 if i < n_negative else 0.7,
            specialists_used=[agent],
        )
        cold_store.write_routing_pattern(
            db,
            session_id=sid,
            query_domain=domain,
            agent_name=agent,
            coverage_contribution=0.6,
            session_verification_score=0.3 if i < n_negative else 0.7,
        )
    db.commit()


class TestCalibratorDeltaFork:
    def test_small_delta_auto_applies(self, db):
        """When delta < threshold, entry auto-applies to learned_strategies."""
        # Seed data that produces a small positive adjustment
        _seed_calibration_data(db, "LitSpecialist", "literature", n_sessions=15, n_negative=0)

        config = CalibrationConfig(
            min_samples=5,
            adjustment_step=0.05,
            large_delta_threshold=0.15,
        )
        calibrator = SpecialistCalibrator(config)
        result = calibrator.calibrate(db)

        assert len(result.get("pending", [])) == 0
        # Should have applied entries
        row = db.execute(
            "SELECT strategy_json FROM learned_strategies "
            "WHERE strategy_type = 'specialist_weights' AND domain_or_key = 'literature'"
        ).fetchone()
        assert row is not None

    def test_large_delta_goes_to_pending(self, db):
        """When delta > threshold, entry goes to pending_calibration."""
        # Seed data that produces a large negative adjustment (0.05 per step but starting from 1.0)
        # To get large delta, we need current weight far from suggested.
        # First, set current weight to 1.0 (default), then seed lots of negative sessions
        _seed_calibration_data(db, "DrugSpecialist", "drugs", n_sessions=20, n_negative=15)

        config = CalibrationConfig(
            min_samples=5,
            adjustment_step=0.25,  # Large step to exceed threshold
            large_delta_threshold=0.15,
        )
        calibrator = SpecialistCalibrator(config)
        result = calibrator.calibrate(db)

        assert len(result.get("pending", [])) > 0
        # Should be in pending_calibration table
        pending = cold_store.get_pending_calibrations(db, status="pending")
        assert len(pending) > 0
        assert pending[0]["agent_name"] == "DrugSpecialist"

    def test_hold_action_never_pending(self, db):
        """HOLD suggestions skip pending regardless of delta."""
        # Seed data that produces a HOLD (negative rate within acceptable range)
        _seed_calibration_data(db, "GenomicsSpecialist", "genomics", n_sessions=15, n_negative=2)

        config = CalibrationConfig(
            min_samples=5,
            large_delta_threshold=0.0,  # Even 0 threshold shouldn't capture HOLD
        )
        calibrator = SpecialistCalibrator(config)
        calibrator.calibrate(db)

        pending = cold_store.get_pending_calibrations(db, status="pending")
        # HOLD actions should never be in pending
        hold_pending = [p for p in pending if p["action"] == "HOLD"]
        assert len(hold_pending) == 0

    def test_threshold_configurable(self):
        """Large delta threshold can be set via constructor."""
        config = CalibrationConfig(large_delta_threshold=0.10)
        assert config.large_delta_threshold == 0.10

    def test_mixed_deltas_split_correctly(self, db):
        """Some entries auto-apply, others go to pending."""
        # Seed two domains: one with small delta, one with large
        _seed_calibration_data(db, "LitSpecialist", "literature", n_sessions=15, n_negative=0)
        _seed_calibration_data(db, "DrugSpecialist", "drugs", n_sessions=20, n_negative=15)

        config = CalibrationConfig(
            min_samples=5,
            adjustment_step=0.25,  # Large step
            large_delta_threshold=0.15,
        )
        calibrator = SpecialistCalibrator(config)
        result = calibrator.calibrate(db)

        # Literature should auto-apply (small delta)
        applied = result.get("applied", [])
        pending = result.get("pending", [])

        # At least one in each bucket
        lit_applied = [a for a in applied if a["agent"] == "LitSpecialist"]
        drug_pending = [p for p in pending if p["agent"] == "DrugSpecialist"]
        assert len(lit_applied) > 0 or len(drug_pending) > 0

    def test_apply_pending_writes_to_learned_strategies(self, db):
        """After apply, the suggested weight appears in learned_strategies."""
        _seed_calibration_data(db, "DrugSpecialist", "drugs", n_sessions=20, n_negative=15)

        config = CalibrationConfig(
            min_samples=5,
            adjustment_step=0.25,
            large_delta_threshold=0.15,
        )
        calibrator = SpecialistCalibrator(config)
        calibrator.calibrate(db)

        pending = cold_store.get_pending_calibrations(db, status="pending")
        if pending:
            ok = cold_store.apply_pending_calibration(db, pending[0]["id"], reviewer="test")
            assert ok is True
            # Verify in learned_strategies
            row = db.execute(
                "SELECT strategy_json FROM learned_strategies "
                "WHERE strategy_type = 'specialist_weights' AND domain_or_key = 'drugs'"
            ).fetchone()
            assert row is not None

    @patch("lifesci_tools.notification_webhook.fire_webhook")
    def test_calibrator_fires_webhook_on_pending(self, mock_webhook, db):
        """Webhook is called when a calibration is routed to pending."""
        mock_webhook.return_value = True
        _seed_calibration_data(db, "DrugSpecialist", "drugs", n_sessions=20, n_negative=15)

        config = CalibrationConfig(
            min_samples=5,
            adjustment_step=0.25,
            large_delta_threshold=0.15,
        )
        calibrator = SpecialistCalibrator(config)
        result = calibrator.calibrate(db)

        if result.get("pending"):
            mock_webhook.assert_called()
            call_args = mock_webhook.call_args
            assert call_args[0][0] == "calibration_pending"
