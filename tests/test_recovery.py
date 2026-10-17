import tempfile
import time
import unittest
import threading
from unittest.mock import patch
from transaction_lab.service import LabService


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.lab = LabService(self.directory.name)
        self.lab.investigator.active = {
            "id": 1,
            "status": "open",
            "hypotheses": [
                {"id": "database_pool", "action": "release_lab_pool_leases"}
            ],
        }
        self.lab.investigator.incidents = [self.lab.investigator.active]

    def tearDown(self):
        self.lab.close()
        self.directory.cleanup()

    def test_approval_requires_matching_unexpired_proposal_and_current_fault(self):
        proposal = self.lab.propose(1, "test-operator")
        with self.assertRaises(ValueError):
            self.lab.approve(proposal["id"], "test-operator")
        self.lab.inject("pool_exhaustion", 10, "test-operator")
        self.lab.proposal["expires_at"] = time.time() - 1
        with self.assertRaises(ValueError):
            self.lab.approve(proposal["id"], "test-operator")
        self.assertEqual(len(self.lab.ledger.pool.held), 4)

    def test_changed_diagnosis_rejects_previously_proposed_action(self):
        proposal = self.lab.propose(1, "test-operator")
        self.lab.inject("pool_exhaustion", 10, "test-operator")
        self.lab.investigator.active["hypotheses"] = [
            {"id": "authorization", "action": None}
        ]
        with self.assertRaises(ValueError):
            self.lab.approve(proposal["id"], "test-operator")

    def test_duplicate_approval_cannot_execute_twice(self):
        proposal = self.lab.propose(1, "test-operator")
        self.lab.inject("pool_exhaustion", 10, "test-operator")
        self.lab.approve(proposal["id"], "test-operator")
        with self.assertRaises(ValueError):
            self.lab.approve(proposal["id"], "test-operator")
        self.assertEqual(len(self.lab.ledger.pool.held), 0)

    def test_no_new_requests_cannot_be_claimed_as_verified_recovery(self):
        proposal = self.lab.propose(1, "test-operator")
        self.lab.inject("pool_exhaustion", 10, "test-operator")
        self.lab.approve(proposal["id"], "test-operator")
        self.lab.proposal["verify_deadline"] = time.time() - 1
        self.lab.check_recovery({"oldest_pending_s": 0})
        self.assertEqual(self.lab.proposal["status"], "verification_failed")

    def test_restart_marks_interrupted_verification_for_review(self):
        proposal = self.lab.propose(1, "test-operator")
        self.lab.inject("pool_exhaustion", 10, "test-operator")
        self.lab.approve(proposal["id"], "test-operator")
        self.lab.close()
        self.lab = LabService(self.directory.name)
        self.assertEqual(self.lab.proposal["status"], "verification_interrupted")

    def test_recent_recovery_preserves_earlier_post_action_misses(self):
        proposal = self.lab.propose(1, "test-operator")
        self.lab.inject("pool_exhaustion", 10, "test-operator")
        self.lab.approve(proposal["id"], "test-operator")
        now = time.time()
        self.lab.proposal["executed_at"] = now - 12
        for index in range(25):
            row, _ = self.lab.ledger.accept(f"recovery-test-{index}", 100, "a" * 32)
            accepted = now - 10 if index == 0 else now - 2
            completed = accepted + 2 if index == 0 else accepted + 0.02
            self.lab.ledger.db.execute(
                "UPDATE transactions SET accepted_at=?,completed_at=?,state='completed' WHERE id=?",
                (accepted, completed, row["id"]),
            )
            self.lab.ledger.db.execute(
                "INSERT INTO postings VALUES(?,?,?)", (row["id"], 100, completed)
            )
        self.lab.ledger.db.commit()
        self.lab.check_recovery({"oldest_pending_s": 0})
        self.assertEqual(self.lab.proposal["status"], "verified")
        self.assertEqual(self.lab.proposal["post_action_bad_requests"], 0)
        self.assertEqual(self.lab.proposal["total_post_action_bad_requests"], 1)

    def test_stopping_load_cannot_overwrite_a_new_session(self):
        entered, release = threading.Event(), threading.Event()

        def blocked_request(*args, **kwargs):
            entered.set()
            release.wait(2)
            return {"transaction_id": "same", "deduplicated": True}

        with patch("transaction_lab.service.request_json", side_effect=blocked_request):
            self.lab.start_load(1, 5, "test-operator")
            self.assertTrue(entered.wait(1))
            with self.lab.lock:
                self.lab.load["running"] = False
            try:
                with self.assertRaises(ValueError):
                    self.lab.start_load(1, 5, "test-operator")
            finally:
                release.set()
                self.lab.load_thread.join(2)
        self.assertFalse(self.lab.load["running"])


if __name__ == "__main__":
    unittest.main()
