from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


SKILLS_ROOT = Path(__file__).resolve().parents[2]
TAVILY_SCRIPTS = SKILLS_ROOT / "tavily-search" / "scripts"
EXTRACT_SCRIPT = TAVILY_SCRIPTS / "extract.mjs"
SEARCH_SCRIPT = TAVILY_SCRIPTS / "search.mjs"


def clean_text(value: str, max_chars: int = 2000) -> str:
    text = " ".join((value or "").split())
    return text[:max_chars]


def split_sentences(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return []
    parts = re.split(r"(?<=[。！？.!?])\s+", compact)
    return [part.strip() for part in parts if part.strip()]


def pick_sentences(text: str, keywords: tuple[str, ...], limit: int = 2) -> list[str]:
    chosen: list[str] = []
    for sentence in split_sentences(text):
        lowered = sentence.casefold()
        if any(keyword in lowered for keyword in keywords):
            chosen.append(clean_text(sentence, 280))
        if len(chosen) >= limit:
            break
    return chosen


def run_json_command(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        raise RuntimeError(stderr)
    return json.loads(proc.stdout)


def extract_brochure(url: str) -> tuple[str, list[str]]:
    if not EXTRACT_SCRIPT.exists():
        raise RuntimeError("Tavily extract script not found")
    data = run_json_command(["node", str(EXTRACT_SCRIPT), url, "--json"])
    texts = [clean_text(item.get("raw_content", ""), 12000) for item in data.get("results", [])]
    failures = [
        clean_text(f"{item.get('url')}: {item.get('error')}", 200)
        for item in data.get("failed_results", [])
    ]
    return "\n".join(text for text in texts if text), failures


def fallback_search(plan_name: str, provider: str) -> tuple[str, list[str]]:
    if not SEARCH_SCRIPT.exists():
        raise RuntimeError("Tavily search script not found")
    query = f"{provider} {plan_name} brochure"
    data = run_json_command(["node", str(SEARCH_SCRIPT), query, "--deep", "--json"])
    answer = clean_text(data.get("answer", ""), 800)
    snippets = [clean_text(item.get("content", ""), 400) for item in data.get("results", [])]
    urls = [item.get("url", "") for item in data.get("results", []) if item.get("url")]
    combined = "\n".join(part for part in [answer, *snippets] if part)
    return combined, urls


def build_research_notes(candidate: dict[str, Any], source_text: str, source_method: str, sources: list[str]) -> dict[str, Any]:
    summary = split_sentences(source_text)[:3]
    if not summary:
        summary = [clean_text(candidate.get("coverage_description", ""), 280)]

    key_benefits = pick_sentences(
        source_text,
        ("cover", "coverage", "benefit", "protect", "dental", "medical", "hospital", "accident", "life", "savings"),
        limit=3,
    )
    if not key_benefits and candidate.get("coverage_description"):
        key_benefits = [clean_text(candidate["coverage_description"], 280)]

    eligibility_notes = pick_sentences(
        source_text,
        ("age", "eligible", "resident", "applicant", "employer", "underwriting", "issue age", "renewal"),
        limit=2,
    )
    if not eligibility_notes:
        fallback = " ".join(
            part for part in [candidate.get("age_text", ""), candidate.get("customer_requirement", "")] if part
        )
        if fallback:
            eligibility_notes = [clean_text(fallback, 280)]

    pricing_notes = pick_sentences(
        source_text,
        ("premium", "annual", "monthly", "hk$", "usd", "mop", "levy", "sum assured"),
        limit=2,
    )
    if not pricing_notes and candidate.get("pricing"):
        pricing_notes = [clean_text(candidate["pricing"], 280)]

    uncertainty_flags: list[str] = []
    if source_method != "extract":
        uncertainty_flags.append("Direct brochure extraction was limited, so fallback research was used.")
    if not source_text:
        uncertainty_flags.append("Brochure details could not be verified through Tavily.")
    if not pricing_notes:
        uncertainty_flags.append("No clear brochure pricing note was found.")

    return {
        "source_method": source_method,
        "sources": sources,
        "summary": summary,
        "key_benefits": key_benefits,
        "eligibility_notes": eligibility_notes,
        "pricing_notes": pricing_notes,
        "uncertainty_flags": uncertainty_flags,
    }


def research_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    brochure_url = candidate.get("brochure_url", "")
    source_text = ""
    sources: list[str] = []
    source_method = "csv_only"
    failures: list[str] = []

    try:
        if brochure_url:
            source_text, failures = extract_brochure(brochure_url)
            if source_text and len(source_text) >= 300:
                source_method = "extract"
                sources = [brochure_url]
            else:
                source_text = ""
    except Exception as exc:
        failures.append(str(exc))

    if not source_text:
        try:
            search_text, search_sources = fallback_search(
                candidate.get("plan_name", ""),
                candidate.get("provider", ""),
            )
            if search_text:
                source_text = search_text
                sources = search_sources or ([brochure_url] if brochure_url else [])
                source_method = "search"
        except Exception as exc:
            failures.append(str(exc))

    notes = build_research_notes(candidate, source_text, source_method, sources)
    if failures:
        notes["uncertainty_flags"].extend(clean_text(item, 180) for item in failures)

    result = dict(candidate)
    result["brochure_research"] = notes
    return result


def research_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [research_candidate(candidate) for candidate in candidates]
