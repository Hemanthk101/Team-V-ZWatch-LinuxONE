"""Durable acceptance, leased work, idempotency, and one posting per transaction.

This models a single database boundary. It does not promise exactly-once effects
against an arbitrary external payment provider.
"""

from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import queue
import sqlite3
import threading
import time
import uuid


class Conflict(ValueError):
    pass


class ConnectionPool:
    """A real, bounded pool. Fault injection leases its actual connections."""

    def __init__(self, path, size=4):
        self.available = queue.Queue(maxsize=size)
        self.held = []
        self.lock = threading.Lock()
        self.size = size
        for _ in range(size):
            connection = sqlite3.connect(path, check_same_thread=False, timeout=2)
            connection.execute("PRAGMA busy_timeout=2000")
            self.available.put(connection)

    @contextmanager
    def connection(self, timeout=0.25):
        connection = self.available.get(timeout=timeout)
        try:
            yield connection
        finally:
            self.available.put(connection)

    def exhaust(self):
        with self.lock:
            while True:
                try:
                    self.held.append(self.available.get_nowait())
                except queue.Empty:
                    break
            return len(self.held)

    def restore(self):
        with self.lock:
            count = len(self.held)
            for connection in self.held:
                self.available.put(connection)
            self.held.clear()
            return count

    def close(self):
        self.restore()
        while not self.available.empty():
            self.available.get_nowait().close()


class Ledger:
    def __init__(self, path, capacity=3000):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS transactions(
                id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                fingerprint TEXT NOT NULL, amount_minor INTEGER NOT NULL,
                accepted_at REAL NOT NULL, completed_at REAL, state TEXT NOT NULL,
                lease_until REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                trace_id TEXT NOT NULL, last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS work_queue ON transactions(state,lease_until,accepted_at);
            CREATE TABLE IF NOT EXISTS postings(
                transaction_id TEXT PRIMARY KEY REFERENCES transactions(id),
                amount_minor INTEGER NOT NULL, posted_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, kind TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS traces(
                id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, trace_id TEXT NOT NULL,
                component TEXT NOT NULL, duration_ms REAL NOT NULL, outcome TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS trace_time ON traces(timestamp);
            CREATE INDEX IF NOT EXISTS trace_identity ON traces(trace_id);
        """)
        # Startup of this single-worker-process service reclaims interrupted work.
        self.db.execute(
            "UPDATE transactions SET state='queued',lease_until=0 WHERE state='processing'"
        )
        self.db.commit()
        self.capacity = capacity
        self.pool = ConnectionPool(path)

    def accept(self, key, amount, trace_id):
        fingerprint = hashlib.sha256(f"TEST:{amount}".encode()).hexdigest()
        with self.lock:
            old = self.db.execute(
                "SELECT * FROM transactions WHERE idempotency_key=?", (key,)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise Conflict(
                        "Idempotency key was already used for a different amount"
                    )
                return dict(old), True
            count = self.db.execute(
                "SELECT count(*) FROM transactions WHERE state!='completed'"
            ).fetchone()[0]
            if count >= self.capacity:
                raise OverflowError("Laboratory backlog limit reached")
            identifier = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO transactions(id,idempotency_key,fingerprint,amount_minor,accepted_at,state,trace_id) VALUES(?,?,?,?,?,'queued',?)",
                (identifier, key, fingerprint, amount, time.time(), trace_id),
            )
            self.db.commit()
            return self.get(identifier), False

    def get(self, identifier):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM transactions WHERE id=?", (identifier,)
            ).fetchone()
            return dict(row) if row else None

    def claim(self):
        with self.lock:
            now = time.time()
            row = self.db.execute(
                "SELECT * FROM transactions WHERE state='queued' AND lease_until<=? ORDER BY accepted_at LIMIT 1",
                (now,),
            ).fetchone()
            if not row:
                return None
            self.db.execute(
                "UPDATE transactions SET state='processing',lease_until=?,attempts=attempts+1 WHERE id=?",
                (now + 5, row["id"]),
            )
            self.db.commit()
            return dict(row)

    def retry(self, identifier, error):
        with self.lock:
            # A failed commit must never requeue an already completed transaction.
            self.db.execute(
                "UPDATE transactions SET state='queued',lease_until=?,last_error=? WHERE id=? AND state='processing'",
                (time.time() + 0.1, error, identifier),
            )
            self.db.commit()

    def complete(self, row):
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute(
                    "INSERT OR IGNORE INTO postings VALUES(?,?,?)",
                    (row["id"], row["amount_minor"], time.time()),
                )
                self.db.execute(
                    "UPDATE transactions SET state='completed',completed_at=?,lease_until=0,last_error=NULL WHERE id=? AND state!='completed'",
                    (time.time(), row["id"]),
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def trace(self, trace_id, component, duration_ms, outcome):
        with self.lock:
            self.db.execute(
                "INSERT INTO traces(timestamp,trace_id,component,duration_ms,outcome) VALUES(?,?,?,?,?)",
                (time.time(), trace_id, component, duration_ms, outcome),
            )
            self.db.commit()

    def event(self, kind, payload):
        with self.lock:
            self.db.execute(
                "INSERT INTO events(timestamp,kind,payload) VALUES(?,?,?)",
                (time.time(), kind, json.dumps(payload)),
            )
            self.db.commit()

    def correctness(self):
        with self.lock:
            scalar = lambda sql: self.db.execute(sql).fetchone()[0]
            return {
                "accepted": scalar("SELECT count(*) FROM transactions"),
                "completed": scalar(
                    "SELECT count(*) FROM transactions WHERE state='completed'"
                ),
                "pending": scalar(
                    "SELECT count(*) FROM transactions WHERE state!='completed'"
                ),
                "postings": scalar("SELECT count(*) FROM postings"),
                "completed_without_posting": scalar(
                    "SELECT count(*) FROM transactions t LEFT JOIN postings p ON t.id=p.transaction_id WHERE t.state='completed' AND p.transaction_id IS NULL"
                ),
                "posting_without_completion": scalar(
                    "SELECT count(*) FROM postings p JOIN transactions t ON t.id=p.transaction_id WHERE t.state!='completed'"
                ),
                "amount_mismatches": scalar(
                    "SELECT count(*) FROM postings p JOIN transactions t ON t.id=p.transaction_id WHERE p.amount_minor!=t.amount_minor"
                ),
                "duplicate_postings": scalar(
                    "SELECT count(*)-count(DISTINCT transaction_id) FROM postings"
                ),
            }

    def recent(self, table, limit=40):
        if table not in ("events", "traces"):
            raise ValueError("Unsupported table")
        with self.lock:
            rows = self.db.execute(
                f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [
                {
                    **dict(row),
                    **(
                        {"payload": json.loads(row["payload"])}
                        if table == "events"
                        else {}
                    ),
                }
                for row in rows
            ]

    def trace_detail(self, trace_id):
        with self.lock:
            spans = self.db.execute(
                "SELECT * FROM traces WHERE trace_id=? ORDER BY id LIMIT 100",
                (trace_id,),
            ).fetchall()
            transaction = self.db.execute(
                "SELECT id,state,accepted_at,completed_at,attempts FROM transactions WHERE trace_id=?",
                (trace_id,),
            ).fetchone()
            return {
                "trace_id": trace_id,
                "spans": [dict(row) for row in spans],
                "transaction": dict(transaction) if transaction else None,
            }

    def snapshot(self, window=15, deadline=0.8):
        now = time.time()
        with self.lock:
            pending = self.db.execute(
                "SELECT count(*),min(accepted_at) FROM transactions WHERE state!='completed'"
            ).fetchone()
            # Mature acceptance cohorts include requests that never completed.
            cohort = self.db.execute(
                "SELECT accepted_at,completed_at FROM transactions WHERE accepted_at>=? AND accepted_at<=?",
                (now - window - deadline, now - deadline),
            ).fetchall()
            bad = sum(r[1] is None or r[1] - r[0] > deadline for r in cohort)
            completed = self.db.execute(
                "SELECT (completed_at-accepted_at)*1000 FROM transactions WHERE completed_at>=?",
                (now - window,),
            ).fetchall()
            accepted = self.db.execute(
                "SELECT count(*) FROM transactions WHERE accepted_at>=?",
                (now - window,),
            ).fetchone()[0]
            spans = self.db.execute(
                "SELECT component,duration_ms,outcome,trace_id FROM traces WHERE timestamp>=?",
                (now - 3,),
            ).fetchall()
            end_latencies = sorted(r[0] for r in completed)
            percentile = lambda values: (
                round(values[min(len(values) - 1, int((len(values) - 1) * 0.95))], 2)
                if values
                else None
            )
            grouped = {}
            for component in ("database.pool", "database.query", "authorization.http"):
                values = sorted(r[1] for r in spans if r[0] == component)
                grouped[component] = {
                    "count": len(values),
                    "p95_ms": percentile(values),
                    "errors": sum(r[2] != "ok" for r in spans if r[0] == component),
                }
            return {
                "timestamp": now,
                "window_s": window,
                "deadline_s": deadline,
                "request_rate": round(accepted / window, 2),
                "completion_rate": round(len(completed) / window, 2),
                "queue_depth": pending[0],
                "oldest_pending_s": round(now - pending[1], 3) if pending[1] else 0,
                "latency_p95_ms": percentile(end_latencies),
                "mature_requests": len(cohort),
                "bad_requests": bad,
                "bad_fraction": bad / len(cohort) if cohort else None,
                "components": grouped,
                "trace_ids": list(dict.fromkeys(row[3] for row in spans))[-12:],
            }

    def prune(self, retention_s=86400):
        """Retain all pending work; prune completed laboratory data after one day."""
        cutoff = time.time() - retention_s
        with self.lock:
            self.db.execute(
                "DELETE FROM postings WHERE transaction_id IN (SELECT id FROM transactions WHERE completed_at<?)",
                (cutoff,),
            )
            self.db.execute("DELETE FROM transactions WHERE completed_at<?", (cutoff,))
            self.db.execute("DELETE FROM traces WHERE timestamp<?", (cutoff,))
            self.db.execute("DELETE FROM events WHERE timestamp<?", (cutoff,))
            self.db.commit()

    def close(self):
        self.pool.close()
        self.db.close()
