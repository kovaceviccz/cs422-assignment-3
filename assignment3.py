import argparse
import csv
import json
import random
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

import numpy as np

from iperf_client import TransferError, run_transfer
from plots import make_plots


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
ALGORITHMS = ("kovacevic", "cubic", "reno", "bbr")
TEST_ORDERS = (
    ("kovacevic", "cubic", "reno", "bbr"),
    ("cubic", "reno", "bbr", "kovacevic"),
    ("reno", "bbr", "kovacevic", "cubic"),
    ("bbr", "kovacevic", "cubic", "reno"),
)
PREFERRED_DESTINATIONS = (
    ("66.35.27.207", 30008),
    ("204.155.243.172", 30005),
    ("114.230.93.99", 5218),
    ("iperf3-vie-at.alwyzon.net", 5205),
    ("148.230.45.213", 30009),
    ("yyc-speedtest.xplore.ca", 8084),
    ("dal.speedtest.clouvider.net", 5203),
    ("speedtest.hiper.dk", 5203),
    ("23.249.54.234", 30005),
    ("96.45.44.87", 30005),
)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Run CS 422 Assignment 3 measurements")
    parser.add_argument("server_list", type=Path, help="JSON list from iperf3serverlist.net")
    parser.add_argument("--destinations", type=int, default=10)
    parser.add_argument("--duration", type=int, default=10, help="seconds per transfer")
    parser.add_argument("--interval", type=float, default=0.5, help="sampling seconds")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--max-attempts", type=int, default=None)
    args = parser.parse_args()
    if args.destinations < 1 or args.duration < 2 or args.interval <= 0 or args.repetitions < 2:
        parser.error("destinations >= 1, duration >= 2, interval > 0, repetitions >= 2")
    if args.interval >= args.duration:
        parser.error("sampling interval must be shorter than the transfer duration")
    if args.max_attempts is not None and args.max_attempts < args.destinations:
        parser.error("max-attempts must be at least the requested destination count")
    return args


def check_environment():
    if shutil.which("typst") is None:
        raise RuntimeError("Typst is required to generate the report PDF")
    for algorithm in ALGORITHMS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as data_socket:
            try:
                data_socket.setsockopt(
                    socket.IPPROTO_TCP, socket.TCP_CONGESTION, algorithm.encode()
                )
            except OSError as error:
                hint = ""
                if algorithm == "kovacevic":
                    hint = "; load it with 'sudo insmod tcp_kovacevic.ko'"
                raise RuntimeError(
                    f"{algorithm} is unavailable on this host: {error}{hint}"
                ) from error


def read_candidates(path):
    entries = json.loads(path.read_text())
    if not isinstance(entries, list):
        raise ValueError("server list must be a JSON array")
    candidates = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        host = str(entry.get("IP/HOST", "")).strip()
        ports = str(entry.get("PORT", "")).strip().split("-")
        if not host or not all(port.isdigit() for port in ports) or len(ports) > 2:
            continue
        first = int(ports[0])
        last = int(ports[-1])
        if not 1 <= first <= last <= 65535 or host in seen:
            continue
        seen.add(host)
        candidates.append({
            "host": host,
            "port": random.randint(first, last),
            "site": entry.get("SITE", ""),
            "country": entry.get("COUNTRY", ""),
        })
    random.shuffle(candidates)
    preferred = []
    for host, port in PREFERRED_DESTINATIONS:
        match = next((candidate for candidate in candidates if candidate["host"] == host), None)
        if match:
            match["port"] = port
            preferred.append(match)
    rest = [candidate for candidate in candidates if candidate not in preferred]
    return preferred + rest


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def run_candidate(candidate, candidate_number, repetitions, duration, interval):
    runs = []
    for repetition in range(repetitions):
        order = TEST_ORDERS[(candidate_number + repetition) % len(TEST_ORDERS)]
        for algorithm in order:
            run_id = f"d{candidate_number:02d}-r{repetition + 1}-{algorithm}"
            print(f"  {run_id}: {candidate['host']}:{candidate['port']}", flush=True)
            try:
                result = run_transfer(
                    candidate["host"], candidate["port"], algorithm,
                    duration, interval, run_id,
                )
            except TransferError as error:
                raise TransferError(f"{run_id}: {error}") from error
            save_json(RESULTS / "raw" / f"{run_id}.json", result)
            runs.append(result)
            time.sleep(0.5)
    return runs


def summarize(complete):
    rows = []
    for destination in complete:
        candidate = destination["candidate"]
        for algorithm in ALGORITHMS:
            runs = [run for run in destination["runs"] if run["algorithm"] == algorithm]
            samples = [sample for run in runs for sample in run["samples"]]
            goodput = [sample["goodput_bps"] / 1_000_000 for sample in samples]
            rtt = [sample["rtt_us"] / 1000 for sample in samples if sample["rtt_us"]]
            rows.append({
                "destination": candidate["host"],
                "port": candidate["port"],
                "algorithm": algorithm,
                "runs": len(runs),
                "samples": len(samples),
                "goodput_min_mbps": min(goodput),
                "goodput_median_mbps": median(goodput),
                "goodput_mean_mbps": mean(goodput),
                "goodput_p95_mbps": float(np.percentile(goodput, 95)),
                "rtt_median_ms": median(rtt) if rtt else None,
                "retransmissions": sum(sample["retransmits_interval"] for sample in samples),
            })
    return rows


def choose_representative(complete):
    for destination in complete:
        cubic_runs = [run for run in destination["runs"] if run["algorithm"] == "cubic"]
        if any(sample["retransmits_interval"] > 0 for run in cubic_runs
               for sample in run["samples"]):
            return destination
    return complete[0]


def write_summary(rows):
    with (RESULTS / "summary.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def compile_report():
    completed = subprocess.run(
        ["typst", "compile", "--root", str(ROOT), str(ROOT / "report.typ"),
         str(RESULTS / "assignment-3-report.pdf")],
        capture_output=True, text=True,
    )
    if completed.returncode:
        raise RuntimeError(f"Typst failed:\n{completed.stderr}")


def main():
    args = parse_arguments()
    check_environment()
    candidates = read_candidates(args.server_list)
    if not candidates:
        raise RuntimeError("server list has no valid hosts and ports")
    attempts = min(len(candidates), args.max_attempts or args.destinations * 5)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "raw").mkdir(exist_ok=True)
    for old in (RESULTS / "raw").glob("*.json"):
        old.unlink()
    for pattern in ("*.pdf", "manifest.json", "summary.csv", "report-data.json"):
        for old in RESULTS.glob(pattern):
            old.unlink()
    manifest = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "kernel": subprocess.check_output(["uname", "-r"], text=True).strip(),
        "server_list": args.server_list.name,
        "requested_destinations": args.destinations,
        "duration_seconds": args.duration,
        "interval_seconds": args.interval,
        "repetitions": args.repetitions,
        "max_candidate_attempts": attempts,
        "algorithm_orders": [],
        "complete_destinations": [],
        "failures": [],
    }
    complete = []
    for candidate_number, candidate in enumerate(candidates[:attempts], start=1):
        if len(complete) == args.destinations:
            break
        print(
            f"Candidate {candidate_number}/{attempts}: "
            f"{candidate['host']}:{candidate['port']}",
            flush=True,
        )
        try:
            runs = run_candidate(
                candidate, candidate_number, args.repetitions, args.duration, args.interval
            )
            complete.append({"candidate": candidate, "runs": runs})
            manifest["complete_destinations"].append(candidate)
            manifest["algorithm_orders"].append({
                "destination": candidate["host"],
                "runs": [{"run_id": run["run_id"], "algorithm": run["algorithm"],
                          "started_utc": run["started_utc"]} for run in runs],
            })
        except TransferError as error:
            failure = {"candidate": candidate, "reason": str(error)}
            manifest["failures"].append(failure)
            print(f"  failed: {error}", flush=True)
        finally:
            manifest["attempted_candidates"] = candidate_number
            manifest["successful_destinations"] = len(complete)
            manifest["shortfall"] = args.destinations - len(complete)
            save_json(RESULTS / "manifest.json", manifest)

    if not complete:
        raise RuntimeError("no destination completed all algorithm runs; see results/manifest.json")
    rows = summarize(complete)
    write_summary(rows)
    representative = choose_representative(complete)
    plots = make_plots(complete, representative, RESULTS)
    rtt_table = []
    for destination in complete:
        host = destination["candidate"]["host"]
        values = {"destination": host}
        for row in rows:
            if row["destination"] == host:
                values[row["algorithm"]] = row["rtt_median_ms"]
        rtt_table.append(values)
    report_data = {
        "manifest": manifest,
        "summary": rows,
        "rtt_table": rtt_table,
        "plots": plots,
        "representative": representative["candidate"],
    }
    save_json(RESULTS / "report-data.json", report_data)
    compile_report()
    print(
        f"Completed {len(complete)}/{args.destinations} destinations. "
        "Report: results/assignment-3-report.pdf"
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
