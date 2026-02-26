"""Tests for DEFRA-adapted learning loops: feedback, calibration, approval gate."""

import json

import pytest

from lifesci_tools import cold_store, cold_worker


@pytest.fixture(autouse=True)
def _clear_caches():
    cold_store.clear_init_cache()
    cold_worker.reset_for_testing()
    yield
    cold_store.clear_init_cache()


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test-learning.db")
    conn = cold_store.get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test-learning.db")


def _seed_session(db, session_id, domain="drugs", specialists=None, verdict="PASS"):
    """Helper: insert a session outcome with routing patterns."""
    specialists = specialists or ["DrugSpecialist", "LiteratureSpecialist"]
    cold_store.write_session_outcome(
        db,
        session_id=session_id,
        query_domain=domain,
        query_text="test query",
        coverage_pct=0.8,
        verification_verdict=verdict,
        verification_score=0.7 if verdict == "PASS" else 0.3,
        specialists_used=specialists,
    )
    for agent in specialists:
        cold_store.write_routing_pattern(
            db,
            session_id=session_id,
            query_domain=domain,
            agent_name=agent,
            coverage_contribution=0.5,
            session_verification_score=0.7 if verdict == "PASS" else 0.3,
        )
    db.commit()


# ── Phase 1: User Feedback CRUD ──────────────────────────────


class TestUserFeedback:
    def test_write_and_read_feedback(self, db):
        _seed_session(db, "sess-1")
        row_id = cold_store.write_user_feedback(
            db,
            session_id="sess-1",
            task_id="task-1",
            feedback_type="down",
            feedback_text="Citations were wrong",
            query_domain="drugs",
            specialists_used=["DrugSpecialist"],
            verification_verdict="PASS",
        )
        assert row_id > 0

        row = db.execute(
            "SELECT * FROM user_feedback WHERE id = ?", (row_id,)
        ).fetchone()
        assert row["session_id"] == "sess-1"
        assert row["feedback_type"] == "down"
        assert row["feedback_text"] == "Citations were wrong"
        assert json.loads(row["specialists_used_json"]) == ["DrugSpecialist"]

    def test_feedback_stats_aggregation(self, db):
        _seed_session(db, "sess-1")
        _seed_session(db, "sess-2")
        _seed_session(db, "sess-3")
        cold_store.write_user_feedback(
            db, session_id="sess-1", feedback_type="up", query_domain="drugs",
        )
        cold_store.write_user_feedback(
            db, session_id="sess-2", feedback_type="down", query_domain="drugs",
            specialists_used=["DrugSpecialist"],
        )
        cold_store.write_user_feedback(
            db, session_id="sess-3", feedback_type="down", query_domain="literature",
            specialists_used=["LiteratureSpecialist"],
        )

        stats = cold_store.get_feedback_stats(db, window_days=90)
        assert stats["total"] == 3
        assert stats["upvotes"] == 1
        assert stats["downvotes"] == 2
        assert "drugs" in stats["per_domain"]
        assert "literature" in stats["per_domain"]

    def test_feedback_stats_agent_filter(self, db):
        _seed_session(db, "sess-1")
        _seed_session(db, "sess-2")
        cold_store.write_user_feedback(
            db, session_id="sess-1", feedback_type="down",
            specialists_used=["DrugSpecialist"],
        )
        cold_store.write_user_feedback(
            db, session_id="sess-2", feedback_type="up",
            specialists_used=["LiteratureSpecialist"],
        )

        stats = cold_store.get_feedback_stats(
            db, window_days=90, agent_name="DrugSpecialist"
        )
        assert stats["total"] == 1
        assert stats["downvotes"] == 1

    def test_recent_feedback_texts(self, db):
        _seed_session(db, "sess-1")
        cold_store.write_user_feedback(
            db, session_id="sess-1", feedback_type="down",
            feedback_text="Missing drug interactions",
            specialists_used=["DrugSpecialist"],
        )
        cold_store.write_user_feedback(
            db, session_id="sess-1", feedback_type="down",
            feedback_text="Outdated dosage info",
            specialists_used=["DrugSpecialist"],
        )

        texts = cold_store.get_recent_feedback_texts(db, "DrugSpecialist", limit=5)
        assert len(texts) == 2
        assert "Missing drug interactions" in texts
        assert "Outdated dosage info" in texts

    def test_feedback_pruning(self, db):
        _seed_session(db, "sess-old")
        cold_store.write_user_feedback(
            db, session_id="sess-old", feedback_type="up",
        )
        # Backdate the record
        db.execute(
            "UPDATE user_feedback SET created_at = datetime('now', '-100 days')"
        )
        db.commit()

        cold_store.prune_cold_store(db, max_age_days=90)
        row = db.execute("SELECT COUNT(*) AS cnt FROM user_feedback").fetchone()
        assert row["cnt"] == 0


# ── Phase 1B: Cold Worker Feedback Dispatch ──────────────────


class TestColdWorkerFeedback:
    def test_persist_feedback(self, db_path):
        # Create the DB and seed a session
        conn = cold_store.get_connection(db_path)
        _seed_session(conn, "sess-fb")
        conn.close()

        cold_worker._persist_feedback(
            db_path,
            {
                "session_id": "sess-fb",
                "task_id": "task-1",
                "feedback_type": "down",
                "feedback_text": "Bad answer",
                "query_domain": "drugs",
                "specialists_used": ["DrugSpecialist"],
                "verification_verdict": "PASS",
            },
        )

        conn = cold_store.get_connection(db_path)
        row = conn.execute("SELECT * FROM user_feedback").fetchone()
        conn.close()
        assert row is not None
        assert row["feedback_type"] == "down"
        assert row["feedback_text"] == "Bad answer"

    def test_persist_one_dispatches_feedback(self, db_path):
        conn = cold_store.get_connection(db_path)
        _seed_session(conn, "sess-dispatch")
        conn.close()

        cold_worker._persist_one(
            db_path,
            {
                "type": "feedback",
                "session_id": "sess-dispatch",
                "feedback_type": "up",
                "cold_db_path": db_path,
            },
        )

        conn = cold_store.get_connection(db_path)
        row = conn.execute("SELECT * FROM user_feedback").fetchone()
        conn.close()
        assert row is not None
        assert row["feedback_type"] == "up"

    def test_feedback_skipped_without_session_id(self, db_path):
        # Should not raise, just log warning
        cold_worker._persist_feedback(db_path, {"feedback_type": "up"})


# ── Phase 2: Specialist Calibrator ──────────────────────────


class TestSpecialistCalibrator:
    def test_calibrate_insufficient_samples(self, db):
        from lifesci_tools.specialist_calibrator import (
            CalibrationConfig,
            SpecialistCalibrator,
        )

        _seed_session(db, "sess-1")
        config = CalibrationConfig(min_samples=10)
        calibrator = SpecialistCalibrator(config=config)
        result = calibrator.calibrate(db)
        assert result["suggestions"] == []

    def test_calibrate_high_negative_rate_lowers_weight(self, db):
        from lifesci_tools.specialist_calibrator import (
            CalibrationConfig,
            SpecialistCalibrator,
        )

        # Create 12 sessions: 5 with thumbs-down + CRITICAL
        for i in range(12):
            sid = f"sess-{i}"
            verdict = "CRITICAL_ISSUES" if i < 5 else "PASS"
            _seed_session(db, sid, domain="drugs", verdict=verdict)
            if i < 5:
                cold_store.write_user_feedback(
                    db, session_id=sid, feedback_type="down",
                    specialists_used=["DrugSpecialist"],
                )

        config = CalibrationConfig(min_samples=5)
        calibrator = SpecialistCalibrator(config=config)
        result = calibrator.calibrate(db)

        drug_suggestions = [
            s for s in result["suggestions"] if s["agent"] == "DrugSpecialist"
        ]
        assert len(drug_suggestions) > 0
        suggestion = drug_suggestions[0]
        assert suggestion["action"] == "LOWER_WEIGHT"
        assert suggestion["weight"] < 1.0

    def test_calibrate_low_negative_rate_raises_weight(self, db):
        from lifesci_tools.specialist_calibrator import (
            CalibrationConfig,
            SpecialistCalibrator,
        )

        # 12 sessions, all PASS, all thumbs-up
        for i in range(12):
            sid = f"sess-{i}"
            _seed_session(db, sid, domain="drugs")
            cold_store.write_user_feedback(
                db, session_id=sid, feedback_type="up",
                specialists_used=["DrugSpecialist"],
            )

        # Set a low initial weight to verify it gets raised
        db.execute(
            """INSERT OR REPLACE INTO learned_strategies
               (strategy_type, domain_or_key, strategy_json, confidence, sample_count)
               VALUES ('specialist_weights', 'drugs', ?, 0.5, 10)""",
            (json.dumps([{"agent": "DrugSpecialist", "weight": 0.7}]),),
        )
        db.commit()

        config = CalibrationConfig(min_samples=5)
        calibrator = SpecialistCalibrator(config=config)
        result = calibrator.calibrate(db)

        drug_suggestions = [
            s for s in result["suggestions"] if s["agent"] == "DrugSpecialist"
        ]
        assert len(drug_suggestions) > 0
        assert drug_suggestions[0]["action"] == "RAISE_WEIGHT"

    def test_weight_bounds_enforced(self, db):
        from lifesci_tools.specialist_calibrator import (
            CalibrationConfig,
            SpecialistCalibrator,
        )

        # Set weight at minimum, all negative feedback
        db.execute(
            """INSERT OR REPLACE INTO learned_strategies
               (strategy_type, domain_or_key, strategy_json, confidence, sample_count)
               VALUES ('specialist_weights', 'drugs', ?, 0.5, 10)""",
            (json.dumps([{"agent": "DrugSpecialist", "weight": 0.3}]),),
        )
        db.commit()

        for i in range(12):
            sid = f"sess-{i}"
            _seed_session(db, sid, domain="drugs", verdict="CRITICAL_ISSUES")
            cold_store.write_user_feedback(
                db, session_id=sid, feedback_type="down",
                specialists_used=["DrugSpecialist"],
            )

        config = CalibrationConfig(min_samples=5, min_weight=0.3)
        calibrator = SpecialistCalibrator(config=config)
        result = calibrator.calibrate(db)

        drug_suggestions = [
            s for s in result["suggestions"] if s["agent"] == "DrugSpecialist"
        ]
        assert len(drug_suggestions) > 0
        assert drug_suggestions[0]["weight"] >= 0.3

    def test_source_calibration(self, db):
        from lifesci_tools.specialist_calibrator import SpecialistCalibrator

        _seed_session(db, "sess-1")
        _seed_session(db, "sess-2")
        cold_store.write_source_reliability(
            db, "sess-1", "pubmed", evidence_count=10, avg_grade_score=3.5,
            citations_valid=8, citations_invalid=2, passed_verification=True,
        )
        cold_store.write_source_reliability(
            db, "sess-2", "pubmed", evidence_count=5, avg_grade_score=3.0,
            citations_valid=4, citations_invalid=1, passed_verification=True,
        )
        db.commit()

        calibrator = SpecialistCalibrator()
        result = calibrator.calibrate_sources(db)
        assert len(result["sources"]) > 0
        pubmed = [s for s in result["sources"] if s["source"] == "pubmed"]
        assert len(pubmed) == 1
        assert 0.3 <= pubmed[0]["weight"] <= 1.0

    def test_seeder_includes_calibration_weights(self, db_path):
        from lifesci_tools.strategy_seeder import build_seed_payload

        conn = cold_store.get_connection(db_path)
        # Seed some data
        for i in range(3):
            _seed_session(conn, f"sess-{i}", domain="drugs")
        cold_store.refresh_learned_strategies(conn)

        # Write calibration weights
        conn.execute(
            """INSERT OR REPLACE INTO learned_strategies
               (strategy_type, domain_or_key, strategy_json, confidence, sample_count)
               VALUES ('specialist_weights', 'drugs', '[]', 0.5, 10)""",
        )
        conn.execute(
            """INSERT OR REPLACE INTO learned_strategies
               (strategy_type, domain_or_key, strategy_json, confidence, sample_count)
               VALUES ('source_weights', '_global', '[]', 0.5, 10)""",
        )
        conn.commit()
        conn.close()

        payload = build_seed_payload("drug interactions aspirin", db_path)
        assert "specialist_weights" in payload
        assert "source_weights" in payload


# ── Phase 3: Human Approval Gate ──────────────────────────


class TestPromptApprovalGate:
    def test_get_pending_candidates_empty(self, db):
        candidates = cold_store.get_pending_candidates(db)
        assert candidates == []

    def test_candidate_lifecycle(self, db):
        # Write a candidate version
        cold_store.write_prompt_version(
            db,
            agent_name="DrugSpecialist",
            version=0,
            instruction="Original prompt",
            is_active=True,
            is_baseline=True,
        )
        cold_store.write_prompt_version(
            db,
            agent_name="DrugSpecialist",
            version=1,
            instruction="Improved prompt",
            is_active=False,
            metadata={"evolved_from": 0, "failure_feedback": "low citation score"},
        )
        # Mark as candidate
        db.execute(
            "UPDATE prompt_versions SET status = 'candidate' "
            "WHERE agent_name = 'DrugSpecialist' AND version = 1"
        )
        db.commit()

        # Verify it appears in pending
        candidates = cold_store.get_pending_candidates(db)
        assert len(candidates) == 1
        assert candidates[0]["version"] == 1
        assert candidates[0]["status"] == "candidate"

        # Approve it
        success = cold_store.approve_candidate(
            db, "DrugSpecialist", 1, reviewer="admin"
        )
        assert success is True

        # Verify it's now active
        active = cold_store.get_active_prompt(db, "DrugSpecialist")
        assert active["version"] == 1

        # Verify no more candidates
        candidates = cold_store.get_pending_candidates(db)
        assert len(candidates) == 0

    def test_reject_candidate(self, db):
        cold_store.write_prompt_version(
            db,
            agent_name="DrugSpecialist",
            version=0,
            instruction="Original",
            is_active=True,
            is_baseline=True,
        )
        cold_store.write_prompt_version(
            db,
            agent_name="DrugSpecialist",
            version=1,
            instruction="Proposed",
            is_active=False,
        )
        db.execute(
            "UPDATE prompt_versions SET status = 'candidate' "
            "WHERE agent_name = 'DrugSpecialist' AND version = 1"
        )
        db.commit()

        success = cold_store.reject_candidate(
            db, "DrugSpecialist", 1, reviewer="admin", reason="Too aggressive"
        )
        assert success is True

        # Original should still be active
        active = cold_store.get_active_prompt(db, "DrugSpecialist")
        assert active["version"] == 0

        # Verify rejected status
        row = db.execute(
            "SELECT status FROM prompt_versions "
            "WHERE agent_name = 'DrugSpecialist' AND version = 1"
        ).fetchone()
        assert row["status"] == "rejected"

    def test_approve_nonexistent_candidate_returns_false(self, db):
        assert cold_store.approve_candidate(db, "FakeAgent", 99) is False

    def test_reject_nonexistent_candidate_returns_false(self, db):
        assert cold_store.reject_candidate(db, "FakeAgent", 99) is False

    def test_status_column_migration(self, db):
        """Verify M4 migration adds status column to prompt_versions."""
        cols = db.execute("PRAGMA table_info(prompt_versions)").fetchall()
        col_names = [c["name"] for c in cols]
        assert "status" in col_names


# ── Phase 3B: Prompt Evolver Human Gate ──────────────────────


class TestPromptEvolverHumanGate:
    def test_human_approval_flag_exists(self):
        from lifesci_tools.prompt_evolver import HUMAN_APPROVAL_REQUIRED

        # Default should be True
        assert isinstance(HUMAN_APPROVAL_REQUIRED, bool)

    def test_build_failure_feedback_includes_user_text(self, db):
        from lifesci_tools.prompt_evolver import PromptEvolver

        _seed_session(db, "sess-1", specialists=["DrugSpecialist"])
        cold_store.write_user_feedback(
            db, session_id="sess-1", feedback_type="down",
            feedback_text="Drug dosages were incorrect",
            specialists_used=["DrugSpecialist"],
        )

        evolver = PromptEvolver(db_path="unused")
        underperf = {
            "agent_name": "DrugSpecialist",
            "avg_composite": 0.4,
            "avg_entity": 0.9,
            "avg_fidelity": 0.5,
            "avg_citation": 0.5,
            "avg_quality": 0.4,
        }
        feedback = evolver._build_failure_feedback(underperf, conn=db)
        assert "Drug dosages were incorrect" in feedback


# ── Feedback Bridge ──────────────────────────────────────────


class TestFeedbackBridge:
    def test_bridge_feedback_to_cold_store(self, db_path):
        """Verify bridge enriches from cold store and enqueues."""
        import os
        from unittest.mock import patch

        from lifesci_tools.feedback_bridge import bridge_feedback_to_cold_store

        # Seed a session in cold store for enrichment
        conn = cold_store.get_connection(db_path)
        _seed_session(conn, "sess-bridge", domain="drugs",
                      specialists=["DrugSpecialist", "LiteratureSpecialist"])
        conn.close()

        with (
            patch.dict(os.environ, {"COLD_DB_PATH": db_path}),
            patch("lifesci_tools.cold_worker.enqueue_session_flush") as mock_enqueue,
        ):
            mock_enqueue.return_value = True
            ok = bridge_feedback_to_cold_store(
                session_id="sess-bridge",
                task_id="task-1",
                feedback_type="down",
                feedback_text="Bad citations",
            )
            assert ok is True
            mock_enqueue.assert_called_once()
            payload = mock_enqueue.call_args[0][0]
            assert payload["type"] == "feedback"
            assert payload["session_id"] == "sess-bridge"
            assert payload["feedback_type"] == "down"
            assert payload["feedback_text"] == "Bad citations"
            # Enrichment from cold store
            assert payload["query_domain"] == "drugs"
            assert "DrugSpecialist" in payload["specialists_used"]

    def test_bridge_without_cold_db_returns_false(self):
        import os
        from unittest.mock import patch

        from lifesci_tools.feedback_bridge import bridge_feedback_to_cold_store

        with patch.dict(os.environ, {"COLD_DB_PATH": ""}):
            ok = bridge_feedback_to_cold_store(session_id="sess-1")
            assert ok is False

    def test_bridge_graceful_when_session_not_in_cold_store(self, db_path):
        """Bridge should still enqueue even if session is unknown."""
        import os
        from unittest.mock import patch

        from lifesci_tools.feedback_bridge import bridge_feedback_to_cold_store

        # Don't seed any session — cold store has no data for this session
        cold_store.get_connection(db_path).close()  # ensure DB exists

        with (
            patch.dict(os.environ, {"COLD_DB_PATH": db_path}),
            patch("lifesci_tools.cold_worker.enqueue_session_flush") as mock_enqueue,
        ):
            mock_enqueue.return_value = True
            ok = bridge_feedback_to_cold_store(
                session_id="unknown-session",
                feedback_type="up",
            )
            assert ok is True
            payload = mock_enqueue.call_args[0][0]
            # Enrichment fields should be empty but not cause errors
            assert payload["query_domain"] == ""
            assert payload["specialists_used"] == []

    def test_double_patch_guard(self):
        """install_feedback_hook should be idempotent."""
        import lifesci_tools.feedback_bridge as fb

        # Reset guard
        fb._HOOK_INSTALLED = False
        try:
            from solace_agent_mesh.gateway.http_sse.services.feedback_service import (
                FeedbackService,
            )
            original = FeedbackService.process_feedback
            fb.install_feedback_hook()
            after_first = FeedbackService.process_feedback
            fb.install_feedback_hook()
            after_second = FeedbackService.process_feedback
            # Second call should NOT re-patch
            assert after_second is after_first
            # Restore
            FeedbackService.process_feedback = original
        except ImportError:
            pass  # FeedbackService not available in test env
        finally:
            fb._HOOK_INSTALLED = False
