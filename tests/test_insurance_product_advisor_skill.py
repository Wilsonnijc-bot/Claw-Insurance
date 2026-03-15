from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "nanobot" / "skills" / "insurance-product-advisor" / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


product_catalog = _load_module("insurance_product_catalog", SCRIPT_ROOT / "product_catalog.py")
brochure_research = _load_module("insurance_brochure_research", SCRIPT_ROOT / "brochure_research.py")


def test_load_catalog_rows_normalizes_spaced_headers() -> None:
    rows = product_catalog.load_catalog_rows([REPO_ROOT / "data" / "dental_insurance.csv"])

    assert rows
    first = rows[0]
    assert "customer_requirement" in first
    assert "price_structure" in first
    assert "additional_informations" in first
    assert " product_brochure_route" not in first
    assert first["product_brochure_route"]


def test_rank_products_returns_missing_fields_for_incomplete_domain() -> None:
    result = product_catalog.rank_products(
        domain="Life Protection",
        facts={"age": "35"},
        catalog_paths=[REPO_ROOT / "data" / "Insurance_datas Mar3.csv"],
    )

    fields = [item["field"] for item in result["missing_fields"]]
    assert result["domain"] == "life_protection"
    assert "health_conditions" in fields
    assert "family_structure" not in fields
    assert any(item["field"] == "family_structure" for item in result["remaining_fields"])
    assert result["candidates"] == []


def test_rank_products_dental_returns_top_candidates_with_brochure_urls() -> None:
    result = product_catalog.rank_products(
        domain="Dental",
        facts={
            "age": "30",
            "residence_location": "Hong Kong",
            "coverage_context": "individual",
        },
        catalog_paths=[REPO_ROOT / "data" / "dental_insurance.csv"],
        limit=3,
    )

    assert result["domain"] == "dental"
    assert result["mapped_categories"] == ["dental"]
    assert result["missing_fields"] == []
    assert 1 <= len(result["candidates"]) <= 3
    assert all(candidate["category"] == "dental" for candidate in result["candidates"])
    assert all(candidate["brochure_url"] for candidate in result["candidates"])


def test_rank_products_can_shortlist_with_domain_plus_two_facts() -> None:
    result = product_catalog.rank_products(
        domain="Dental",
        facts={
            "age": "30",
            "residence_location": "Hong Kong",
        },
        catalog_paths=[REPO_ROOT / "data" / "dental_insurance.csv"],
        limit=3,
    )

    assert result["missing_fields"] == []
    assert any(item["field"] == "coverage_context" for item in result["remaining_fields"])
    assert 1 <= len(result["candidates"]) <= 3


def test_research_candidate_falls_back_to_search_when_extract_is_thin(monkeypatch) -> None:
    candidate = {
        "plan_name": "Test Dental Plan",
        "provider": "AIA HK",
        "brochure_url": "https://example.com/brochure.pdf",
        "coverage_description": "Basic dental cover",
        "pricing": "HK$100/year",
        "age_text": "18-60",
        "customer_requirement": "Hong Kong residents",
    }

    monkeypatch.setattr(brochure_research, "extract_brochure", lambda url: ("too short", []))
    monkeypatch.setattr(
        brochure_research,
        "fallback_search",
        lambda plan, provider: (
            "This plan covers routine dental treatment. Premium is HK$100 per year. Eligible ages are 18 to 60.",
            ["https://example.com/search-result"],
        ),
    )

    result = brochure_research.research_candidate(candidate)

    assert result["brochure_research"]["source_method"] == "search"
    assert result["brochure_research"]["sources"] == ["https://example.com/search-result"]
    assert result["brochure_research"]["summary"]
    assert result["brochure_research"]["pricing_notes"]


def test_research_candidate_falls_back_to_csv_only_when_tavily_fails(monkeypatch) -> None:
    candidate = {
        "plan_name": "Fallback Plan",
        "provider": "AIA HK",
        "brochure_url": "https://example.com/brochure.pdf",
        "coverage_description": "Hospital and dental cover",
        "pricing": "HK$200/year",
        "age_text": "18-55",
        "customer_requirement": "Hong Kong residents only",
    }

    def _boom(*args, **kwargs):
        raise RuntimeError("Missing TAVILY_API_KEY")

    monkeypatch.setattr(brochure_research, "extract_brochure", _boom)
    monkeypatch.setattr(brochure_research, "fallback_search", _boom)

    result = brochure_research.research_candidate(candidate)
    notes = result["brochure_research"]

    assert notes["source_method"] == "csv_only"
    assert notes["pricing_notes"] == ["HK$200/year"]
    assert any("could not be verified" in item.casefold() for item in notes["uncertainty_flags"])
