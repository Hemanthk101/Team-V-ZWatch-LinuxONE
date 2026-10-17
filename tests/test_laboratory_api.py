import json
import os
import unittest
import uuid
import time
import urllib.error
import urllib.request
from pathlib import Path
import test_api


@unittest.skipUnless(
    os.environ.get("ZWATCH_TEST_URL"),
    "Set ZWATCH_TEST_URL for deployed integration checks",
)
class LaboratoryAPITests(unittest.TestCase):
    request = test_api.APITests.request

    @classmethod
    def setUpClass(cls):
        cls.url = os.environ["ZWATCH_TEST_URL"].rstrip("/")
        cls.key = Path(os.environ["ZWATCH_KEY_FILE"]).read_text().strip()

    def test_laboratory_read_and_write_require_authentication(self):
        self.assertEqual(
            self.request("/api/transaction-lab/state", token=False)[0], 401
        )
        self.assertEqual(
            self.request(
                "/api/transaction-lab/fault",
                "POST",
                {"kind": "pool_exhaustion"},
                token=False,
            )[0],
            401,
        )

    def test_cross_origin_fault_injection_rejected(self):
        self.assertEqual(
            self.request(
                "/api/transaction-lab/fault",
                "POST",
                {"kind": "pool_exhaustion"},
                origin="https://foreign.invalid",
            )[0],
            403,
        )

    def test_unbounded_load_and_unknown_faults_rejected(self):
        self.assertEqual(
            self.request(
                "/api/transaction-lab/load", "POST", {"rps": 1000, "duration_s": 3600}
            )[0],
            422,
        )
        self.assertEqual(
            self.request(
                "/api/transaction-lab/fault", "POST", {"kind": "shell_command"}
            )[0],
            422,
        )
        self.assertEqual(
            self.request(
                "/api/transaction-lab/fault",
                "POST",
                {"kind": "pool_exhaustion", "duration_s": 300},
            )[0],
            422,
        )

    def test_state_declares_laboratory_scope_and_freshness(self):
        status, payload = self.request("/api/transaction-lab/state")
        self.assertEqual(status, 200)
        state = json.loads(payload)
        self.assertIn("no real payments", state["scope"])
        self.assertIn(state["freshness"]["status"], ("healthy", "unknown"))
        self.assertNotIn("fault", state["latest"]["analysis"])

    def test_internal_service_rejects_dashboard_token(self):
        request = urllib.request.Request(
            "http://127.0.0.1:38901/state",
            headers={"Authorization": "Bearer " + self.key},
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=4)
        self.assertEqual(error.exception.code, 401)

    def test_real_http_idempotency_conflict_and_input_validation(self):
        from transaction_lab.common import request_json

        key = "integration-" + uuid.uuid4().hex
        first = request_json(
            "http://127.0.0.1:38901/transactions",
            "POST",
            {"idempotency_key": key, "amount_minor": 123},
        )
        second = request_json(
            "http://127.0.0.1:38901/transactions",
            "POST",
            {"idempotency_key": key, "amount_minor": 123},
        )
        self.assertEqual(first["transaction_id"], second["transaction_id"])
        self.assertTrue(second["deduplicated"])
        with self.assertRaises(urllib.error.HTTPError) as error:
            request_json(
                "http://127.0.0.1:38901/transactions",
                "POST",
                {"idempotency_key": key, "amount_minor": 999},
            )
        self.assertEqual(error.exception.code, 409)
        with self.assertRaises(urllib.error.HTTPError) as error:
            request_json(
                "http://127.0.0.1:38901/transactions",
                "POST",
                {"idempotency_key": key + "-new", "amount_minor": 1.5},
            )
        self.assertEqual(error.exception.code, 422)

    def test_trace_reference_resolves_to_recorded_operations(self):
        from transaction_lab.common import request_json

        key = "trace-check-" + uuid.uuid4().hex
        accepted = request_json(
            "http://127.0.0.1:38901/transactions",
            "POST",
            {"idempotency_key": key, "amount_minor": 123},
        )
        # This integration suite runs with an idle laboratory, outside benchmarks.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, payload = self.request(
                "/api/transaction-lab/trace/" + accepted["trace_id"]
            )
            self.assertEqual(status, 200)
            trace = json.loads(payload)
            if trace["spans"]:
                break
            time.sleep(0.05)
        self.assertEqual(trace["transaction"]["id"], accepted["transaction_id"])
        self.assertTrue(trace["spans"])

    def test_invalid_trace_id_is_rejected(self):
        self.assertEqual(self.request("/api/transaction-lab/trace/not-a-trace")[0], 422)


if __name__ == "__main__":
    unittest.main()
