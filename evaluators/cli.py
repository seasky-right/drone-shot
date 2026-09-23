"""Local, simulator-free entry points for episode evaluation and comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from evaluators.benchmark import markdown_report, summarize_results
from evaluators.record import evaluate_record, read_record, write_json


def _safe_segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or value in (".", ".."):
        raise ValueError(f"identifier cannot be used as a result path: {value!r}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="ReachPoint offline evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate", help="score one recorded episode")
    evaluate.add_argument("record", type=Path)
    evaluate.add_argument("--output", required=True, type=Path)
    batch = commands.add_parser("evaluate-batch", help="score all episode JSON records in a directory")
    batch.add_argument("records", type=Path)
    batch.add_argument("--output-dir", required=True, type=Path)
    summarize = commands.add_parser("summarize", help="compare result.json files")
    summarize.add_argument("results", type=Path, help="directory searched recursively for result.json")
    summarize.add_argument("--output", required=True, type=Path)
    summarize.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "evaluate":
        record = read_record(args.record)
        result = evaluate_record(record, str(args.record.resolve()))
        write_json(args.output, result)
        print(f"{args.output}: score={result['scores']['total']:.2f}, reason={result['termination_reason']}")
    elif args.command == "evaluate-batch":
        if args.output_dir.resolve().is_relative_to(args.records.resolve()):
            parser.error("output directory must be outside the input record directory")
        paths = sorted(args.records.rglob("*.json"))
        if not paths:
            parser.error("no episode JSON records found")
        pending = []
        outputs = set()
        for path in paths:
            record = read_record(path)
            result = evaluate_record(record, str(path.resolve()))
            destination = (args.output_dir / _safe_segment(record.case_id)
                           / _safe_segment(record.agent_id) / str(record.repeat_index) / "result.json")
            if destination in outputs:
                parser.error(f"duplicate episode identity: {destination}")
            outputs.add(destination)
            pending.append((destination, result))
        for destination, result in pending:
            write_json(destination, result)
        print(f"{args.output_dir}: evaluated {len(pending)} episodes")
    else:
        paths = sorted(args.results.rglob("result.json"))
        if not paths:
            parser.error("no result.json files found")
        results = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        summary = summarize_results(results)
        write_json(args.output, summary)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(markdown_report(summary), encoding="utf-8")
        print(f"{args.output}: {len(paths)} attempts, ranking={summary['ranking_status']}")


if __name__ == "__main__":
    main()
