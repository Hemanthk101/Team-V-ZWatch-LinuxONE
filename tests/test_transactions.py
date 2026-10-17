"""Correctness and investigation invariants, independent of the injected labels."""

import concurrent.futures
import queue
import tempfile
import time
import unittest
from pathlib import Path
from transaction_lab.ledger import Ledger, Conflict
from transaction_lab.analysis import Investigator, baselines, investigate


def observation(timestamp=100, component="database.pool", bad=0.5, oldest=2):
    components = {
        name: {"count": 15, "p95_ms": 2, "errors": 0}
        for name in ("database.pool", "database.query", "authorization.http")
    }
    if component:
        components[component].update(p95_ms=300, errors=12)
    return {
        "timestamp": timestamp,
        "components": components,
        "oldest_pending_s": oldest,
        "queue_depth": 12 if oldest else 0,
        "mature_requests": 30,
        "bad_requests": int(30 * bad),
        "bad_fraction": bad,
    }


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)

    def tearDown(self):
        self.ledger.close()
        self.directory.cleanup()

    def test_concurrent_duplicate_acceptance_creates_one_transaction(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda _: self.ledger.accept("same-key-123", 100, "a" * 32),
                    range(30),
                )
            )
        self.assertEqual(len({r[0]["id"] for r in results}), 1)
        self.assertEqual(sum(not r[1] for r in results), 1)
        self.assertEqual(self.ledger.correctness()["accepted"], 1)

    def test_conflicting_idempotency_key_is_rejected(self):
        self.ledger.accept("same-key-123", 100, "a" * 32)
        with self.assertRaises(Conflict):
            self.ledger.accept("same-key-123", 200, "a" * 32)

    def test_repeat_completion_and_retry_cannot_duplicate_or_requeue(self):
        row, _ = self.ledger.accept("same-key-123", 100, "a" * 32)
        row = self.ledger.claim()
        self.ledger.complete(row)
        self.ledger.complete(row)
        self.ledger.retry(row["id"], "ambiguous response")
        report = self.ledger.correctness()
        self.assertEqual(report["completed"], 1)
        self.assertEqual(report["postings"], 1)
        self.assertEqual(report["pending"], 0)
        self.assertEqual(report["amount_mismatches"], 0)

    def test_interrupted_processing_is_reclaimed_on_restart(self):
        row, _ = self.ledger.accept("same-key-123", 100, "a" * 32)
        self.ledger.claim()
        self.ledger.close()
        self.ledger = Ledger(self.path)
        claimed = self.ledger.claim()
        self.assertEqual(claimed["id"], row["id"])
        self.ledger.complete(claimed)
        self.assertEqual(self.ledger.correctness()["postings"], 1)

    def test_incomplete_requests_remain_in_deadline_denominator(self):
        row, _ = self.ledger.accept("same-key-123", 100, "a" * 32)
        self.ledger.db.execute(
            "UPDATE transactions SET accepted_at=? WHERE id=?",
            (time.time() - 2, row["id"]),
        )
        self.ledger.db.commit()
        snapshot = self.ledger.snapshot()
        self.assertEqual(snapshot["mature_requests"], 1)
        self.assertEqual(snapshot["bad_fraction"], 1)

    def test_actual_pool_leases_timeout_then_restore(self):
        self.assertEqual(self.ledger.pool.exhaust(), 4)
        with self.assertRaises(queue.Empty):
            with self.ledger.pool.connection(timeout=0.01):
                pass
        self.assertEqual(self.ledger.pool.restore(), 4)
        with self.ledger.pool.connection(timeout=0.01) as connection:
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)

    def test_retention_never_deletes_pending_work(self):
        row, _ = self.ledger.accept("same-key-123", 100, "a" * 32)
        self.ledger.db.execute(
            "UPDATE transactions SET accepted_at=?", (time.time() - 172800,)
        )
        self.ledger.db.commit()
        self.ledger.prune()
        self.assertIsNotNone(self.ledger.get(row["id"]))

    def test_failed_transaction_commit_does_not_leave_half_a_posting(self):
        self.ledger.accept("commit-failure-123", 100, "a" * 32)
        row = self.ledger.claim()
        self.ledger.db.execute(
            "CREATE TRIGGER reject_completion BEFORE UPDATE ON transactions WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT,'injected storage failure'); END"
        )
        with self.assertRaises(Exception):
            self.ledger.complete(row)
        self.assertEqual(self.ledger.correctness()["postings"], 0)
        self.assertEqual(self.ledger.get(row["id"])["state"], "processing")


class InvestigationTests(unittest.TestCase):
    def test_diagnosis_uses_observations_without_fault_labels(self):
        self.assertEqual(investigate(observation())[0]["id"], "database_pool")
        self.assertEqual(
            investigate(observation(component="authorization.http"))[0]["id"],
            "authorization",
        )
        self.assertEqual(
            investigate(observation(component=None))[0]["id"], "worker_progress"
        )

    def test_pool_incident_can_occur_with_healthy_host_utilization(self):
        s = observation()
        comparisons = baselines(s, s, {"cpu_pct": 10, "memory_pct": 40})
        self.assertFalse(comparisons["host_threshold"])
        self.assertTrue(comparisons["slo_burn"])

    def test_elapsed_time_and_gaps_control_incident_confirmation(self):
        investigator = Investigator()
        for stamp in (100, 100.1, 100.2):
            s = observation(stamp)
            result = investigator.observe(s, baselines(s, s, {}))
            self.assertFalse(result["confirmed"])
        s = observation(104)
        self.assertFalse(investigator.observe(s, baselines(s, s, {}))["confirmed"])
        for stamp in (105, 106):
            s = observation(stamp)
            result = investigator.observe(s, baselines(s, s, {}))
        self.assertTrue(result["confirmed"])

    def test_healthy_traffic_surge_does_not_open_incident(self):
        investigator = Investigator()
        for stamp in range(100, 110):
            s = observation(stamp, component=None, bad=0, oldest=0)
            self.assertFalse(investigator.observe(s, baselines(s, s, {}))["confirmed"])

    def test_missing_service_data_is_not_a_zero_error_measurement(self):
        s = observation(component=None, bad=0, oldest=0)
        s.update(mature_requests=0, bad_fraction=None)
        comparisons = baselines(s, s, {})
        self.assertFalse(comparisons["slo_has_enough_requests"])

    def test_wall_clock_changes_do_not_accelerate_monotonic_confirmation(self):
        investigator = Investigator()
        for wall, monotonic in ((100, 10), (200, 10.1), (300, 10.2)):
            s = observation(wall)
            s["monotonic_s"] = monotonic
            self.assertFalse(investigator.observe(s, baselines(s, s, {}))["confirmed"])

    def test_opening_evidence_is_preserved_when_current_hypothesis_changes(self):
        investigator = Investigator()
        for stamp in (100, 101, 102):
            s = observation(stamp)
            investigator.observe(s, baselines(s, s, {}))
        initial = investigator.active["opening_evidence"]
        s = observation(103, component="authorization.http")
        investigator.observe(s, baselines(s, s, {}))
        self.assertEqual(investigator.active["hypotheses"][0]["id"], "authorization")
        self.assertEqual(initial["components"]["database.pool"]["errors"], 12)


if __name__ == "__main__":
    unittest.main()
