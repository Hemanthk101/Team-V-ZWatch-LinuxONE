"""Run bounded real-service experiments; never consume labels in the detector.

Run on the LinuxONE host within the laboratory container:
python -m transaction_lab.benchmark --repeats 3 --output data/transaction-lab/benchmark.json
Requires the laboratory to be idle. This is a small controlled benchmark, not a
production accuracy or business-impact claim.
"""

import argparse
import hashlib
import json
import platform
import statistics
import time
from pathlib import Path
from transaction_lab.common import request_json

URL = "http://127.0.0.1:38901"
METHODS = ("host_threshold", "service_threshold", "slo_burn", "zwatch")


def save_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2))
    temporary.replace(path)


def call(path, body=None):
    return request_json(
        URL + path, "POST" if body is not None else "GET", body, timeout=8
    )


def sample_for(duration, points):
    until = time.monotonic() + duration
    while time.monotonic() < until:
        state = call("/state")
        latest = state["latest"]
        if not points or points[-1]["timestamp"] != latest["timestamp"]:
            points.append(latest)
        time.sleep(0.5)
    return state


def flags(point):
    return {
        **{m: point["comparators"][m] for m in METHODS[:-1]},
        "zwatch": point["analysis"]["confirmed"],
    }


def trial(kind, repeat):
    call("/control/stop", {})
    call("/control/clear", {})
    points = []
    # Allow the previous cohort and confirmed incident to clear before the trial.
    sample_for(20, points)
    start = time.time()
    call(
        "/control/load",
        {"rps": 12 if kind != "healthy_surge" else 30, "duration_s": 100},
    )
    points = []
    initial = sample_for(12, points)
    if initial["latest"]["oldest_pending_s"] > 0.8 or any(
        i["status"] == "open" for i in initial["incidents"]
    ):
        raise RuntimeError("A trial must begin with healthy, drained service")
    onset = time.time()
    action = None
    fault = None
    if kind != "healthy_surge":
        fault = call("/control/fault", {"kind": kind, "duration_s": 30})
        onset = fault["started_at"]
        state = sample_for(8, points)
        if kind == "pool_exhaustion":
            active = next(
                (i for i in reversed(state["incidents"]) if i["status"] == "open"), None
            )
            if active:
                proposal = call("/recovery/propose", {"incident_id": active["id"]})
                action = call("/recovery/approve", {"proposal_id": proposal["id"]})
        cleared_at = time.time()
        call("/control/clear", {})
    else:
        state = sample_for(8, points)
        cleared_at = time.time()
    state = sample_for(20, points)
    call("/control/stop", {})
    state = sample_for(2, points)
    end = time.time()
    # Compare equal observation windows, preserving each method's actual policy.
    fault_points = [p for p in points if onset <= p["timestamp"] <= cleared_at]
    healthy_points = [p for p in points if p["timestamp"] < onset]
    outcome = {}
    for method in METHODS:
        detected = next((p for p in fault_points if flags(p)[method]), None)
        outcome[method] = {
            "detected_during_fault": detected is not None if fault else None,
            "delay_s": (
                round(detected["timestamp"] - onset, 3) if detected and fault else None
            ),
            "healthy_alert_observations": sum(
                bool(flags(p)[method])
                for p in (points if not fault else healthy_points)
            ),
            "healthy_observations": len(points if not fault else healthy_points),
        }
    relevant = [i for i in state["incidents"] if onset <= i["opened_at"] <= cleared_at]
    return {
        "kind": kind,
        "repeat": repeat,
        "started_at": start,
        "fault_started_at": onset if fault else None,
        "fault_cleared_at": cleared_at if fault else None,
        "finished_at": end,
        "comparisons": outcome,
        "leading_hypothesis": relevant[0]["hypotheses"][0]["id"] if relevant else None,
        "recovery": state["proposal"] if action else None,
        "correctness": state["correctness"],
        "load": state["load"],
        "max_host_cpu_pct": max(p["host"]["cpu_pct"] for p in points),
        "analysis_p95_ms": sorted(p["analysis_ms"] for p in points)[
            int((len(points) - 1) * 0.95)
        ],
        "service_rss_mib": state["runtime"]["rss_mib"],
        "observations": points,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 6))
    parser.add_argument(
        "--output", type=Path, default=Path("data/transaction-lab/benchmark.json")
    )
    args = parser.parse_args()
    state = call("/state")
    if state["load"]["running"] or state["laboratory_fault"]:
        raise RuntimeError("Wait until the laboratory is idle before benchmarking")
    report = {
        "schema_version": 1,
        "status": "running",
        "expected_trials": args.repeats * 4,
        "started_at": time.time(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "scope": "Generated HTTP workload and deliberately injected faults in running services; no production/customer data",
        "methodology": {
            "deadline_s": 0.8,
            "slo_target": 0.99,
            "request_rates": [12, 30],
            "fault_duration_s": 8,
            "recovery_observation_s": 20,
            "slo_windows_s": [3, 15],
            "slo_burn_threshold": 14.4,
            "diagnosis": "Fixed evidence rules, no fault labels as features; no fitted ML in transaction investigation",
            "limits": "Small laboratory sample; same host; fixed fault types; no production extrapolation; no parameters tuned during benchmark",
        },
        "trials": [],
        "source_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path("transaction_lab").glob("*.py")
        },
    }
    try:
        for repeat in range(1, args.repeats + 1):
            for kind in (
                "pool_exhaustion",
                "dependency_delay",
                "worker_pause",
                "healthy_surge",
            ):
                print(
                    f"Starting {kind}, repetition {repeat}/{args.repeats}", flush=True
                )
                result = trial(kind, repeat)
                report["trials"].append(result)
                save_report(args.output, report)
                print(
                    json.dumps(
                        {
                            "kind": kind,
                            "repeat": repeat,
                            "comparisons": result["comparisons"],
                            "hypothesis": result["leading_hypothesis"],
                            "recovery": (
                                result["recovery"]["status"]
                                if result["recovery"]
                                else None
                            ),
                            "pending": result["correctness"]["pending"],
                        }
                    ),
                    flush=True,
                )
        report["status"] = "completed"
    except BaseException:
        report["status"] = "interrupted"
        raise
    finally:
        for path in ("/control/stop", "/control/clear"):
            try:
                call(path, {})
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(type(error).__name__)
        report["finished_at"] = time.time()
        save_report(args.output, report)


if __name__ == "__main__":
    main()
