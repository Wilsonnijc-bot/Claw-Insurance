#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

from product_catalog import load_json, rank_products


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shortlist insurance products from the configured catalog.")
    parser.add_argument("--domain", required=True, help="Insurance domain, e.g. Dental or Life Protection")
    parser.add_argument("--facts-json", help="Raw JSON object of collected facts")
    parser.add_argument("--facts-file", type=Path, help="Path to a JSON file of collected facts")
    parser.add_argument(
        "--catalog",
        action="append",
        dest="catalogs",
        type=Path,
        help="Override catalog CSV path for tests or local development",
    )
    parser.add_argument("--limit", type=int, default=3, help="Maximum candidates to return")
    return parser.parse_args()


def load_facts(args: argparse.Namespace) -> dict:
    if args.facts_file:
        return load_json(args.facts_file)
    if args.facts_json:
        return json.loads(args.facts_json)
    return {}


def main() -> None:
    args = parse_args()
    facts = load_facts(args)
    result = rank_products(
        domain=args.domain,
        facts=facts,
        catalog_paths=args.catalogs,
        limit=args.limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
