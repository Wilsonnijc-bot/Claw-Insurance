#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

from brochure_research import research_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research shortlisted insurance products via brochure URLs.")
    parser.add_argument("--candidates-json", help="Raw JSON array of shortlisted candidates")
    parser.add_argument("--candidates-file", type=Path, help="Path to a JSON file containing shortlisted candidates")
    return parser.parse_args()


def load_candidates(args: argparse.Namespace) -> list[dict]:
    if args.candidates_file:
        return json.loads(args.candidates_file.read_text(encoding="utf-8"))
    if args.candidates_json:
        return json.loads(args.candidates_json)
    return []


def main() -> None:
    args = parse_args()
    candidates = load_candidates(args)
    researched = research_candidates(candidates)
    print(json.dumps({"candidates": researched}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
