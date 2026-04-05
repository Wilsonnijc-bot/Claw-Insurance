from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nanobot.insurance_catalog import (
    CatalogRepository,
    CatalogUnavailableError,
    CsvCatalogRepository,
    get_default_catalog_repository,
)

from nanobot.utils.paths import project_root

REPO_ROOT = project_root()
DEFAULT_CATALOGS = [
    REPO_ROOT / "data" / "Insurance_datas Mar3.csv",
    REPO_ROOT / "data" / "dental_insurance.csv",
]

DOMAIN_CONFIG: dict[str, dict[str, Any]] = {
    "dental": {
        "display": "Dental",
        "categories": ["dental"],
        "required_fields": ["age", "residence_location", "coverage_context"],
        "activation_min_fields": 2,
        "activation_required_fields": [],
        "questions": {
            "age": "想幫你揀得準啲，方便講一下受保人大概幾多歲嗎？",
            "residence_location": "受保人主要居住地係邊度？例如香港、澳門或其他地區。",
            "coverage_context": "你想了解個人牙科保障，定係僱員／公司團體相關方案？",
        },
    },
    "health_medical": {
        "display": "Health / Medical",
        "categories": ["health", "group medical insurance"],
        "required_fields": ["age", "health_conditions", "residence_location"],
        "activation_min_fields": 2,
        "activation_required_fields": [],
        "questions": {
            "age": "受保人大概幾多歲？",
            "health_conditions": "現時有冇已知健康狀況、長期病患，或者近期需要特別留意的醫療情況？",
            "residence_location": "受保人主要居住地係香港、澳門，定其他地區？",
        },
    },
    "critical_illness": {
        "display": "Critical Illness",
        "categories": ["critical illness"],
        "required_fields": ["age", "health_conditions", "desired_coverage_amount"],
        "activation_min_fields": 2,
        "activation_required_fields": [],
        "questions": {
            "age": "受保人大概幾多歲？",
            "health_conditions": "有冇任何現有健康狀況或者病歷需要先留意？",
            "desired_coverage_amount": "你心目中想要幾大保障額？例如 200萬、500萬 或 1,000萬。",
        },
    },
    "life_protection": {
        "display": "Life Protection",
        "categories": ["whole life", "term life"],
        "required_fields": [
            "age",
            "health_conditions",
            "family_structure",
            "income_role",
            "desired_payout",
            "beneficiaries",
        ],
        "activation_min_fields": 2,
        "activation_required_fields": [],
        "questions": {
            "age": "受保人大概幾多歲？",
            "health_conditions": "有冇任何現有健康狀況或者病歷需要先留意？",
            "family_structure": "家庭狀況大概係點？例如已婚、有冇小朋友、需要照顧邊類家人。",
            "income_role": "受保人喺家庭收入入面係主要支柱、部分支柱，定比較次要？",
            "desired_payout": "如果真係要賠償，你心目中大概希望留低幾多保障額？",
            "beneficiaries": "你主要想保障邊位受益人？例如配偶、小朋友、父母。",
        },
    },
    "savings_retirement": {
        "display": "Savings / Retirement",
        "categories": ["savings", "deferred annuity", "retirement income"],
        "required_fields": [
            "location_of_funds",
            "investment_amount",
            "wealth_goals",
            "growth_expectations",
        ],
        "activation_min_fields": 2,
        "activation_required_fields": [],
        "questions": {
            "location_of_funds": "資金主要會由邊個地區安排？例如香港、澳門或其他地方。",
            "investment_amount": "你打算大概放幾多資金做呢個儲蓄／退休安排？",
            "wealth_goals": "今次主要目標係退休收入、穩健增值、教育金，定資產傳承？",
            "growth_expectations": "你對回報取向偏向穩健、平衡，定希望增長性高啲？",
        },
    },
    "general_protection_non_life": {
        "display": "General Protection / Non-Life",
        "categories": [
            "personal accident",
            "accident protection",
            "personal liability",
            "domestic worker insurance",
            "domestic helper insurance",
            "golf insurance",
        ],
        "required_fields": ["subtype", "asset_details", "asset_usage", "asset_location"],
        "activation_min_fields": 2,
        "activation_required_fields": ["subtype"],
        "questions": {
            "subtype": "你想了解邊一類非壽險保障？例如意外、責任、外傭、家居責任或高爾夫相關。",
            "asset_details": "想保障的是甚麼資產或風險對象？可以簡單講一下。",
            "asset_usage": "呢個資產／保障對象主要用途係乜？例如自住、出租、工作用途或家庭用途。",
            "asset_location": "相關資產或風險主要喺邊個地區？",
        },
    },
}

DOMAIN_ALIASES = {
    "dental": "dental",
    "health / medical": "health_medical",
    "health": "health_medical",
    "medical": "health_medical",
    "health medical": "health_medical",
    "critical illness": "critical_illness",
    "ci": "critical_illness",
    "life protection": "life_protection",
    "life": "life_protection",
    "term life": "life_protection",
    "whole life": "life_protection",
    "savings / retirement": "savings_retirement",
    "savings": "savings_retirement",
    "retirement": "savings_retirement",
    "annuity": "savings_retirement",
    "general protection / non-life": "general_protection_non_life",
    "general protection": "general_protection_non_life",
    "non-life": "general_protection_non_life",
    "non life": "general_protection_non_life",
}

FACT_KEY_ALIASES = {
    "age": "age",
    "residence_location": "residence_location",
    "location": "residence_location",
    "residence": "residence_location",
    "coverage_context": "coverage_context",
    "coverage_type": "coverage_context",
    "individual_vs_employee_group": "coverage_context",
    "health_conditions": "health_conditions",
    "health": "health_conditions",
    "desired_coverage_amount": "desired_coverage_amount",
    "coverage_amount": "desired_coverage_amount",
    "family_structure": "family_structure",
    "income_role": "income_role",
    "desired_payout": "desired_payout",
    "beneficiaries": "beneficiaries",
    "location_of_funds": "location_of_funds",
    "funds_location": "location_of_funds",
    "investment_amount": "investment_amount",
    "wealth_goals": "wealth_goals",
    "growth_expectations": "growth_expectations",
    "asset_details": "asset_details",
    "asset_value": "asset_details",
    "asset_usage": "asset_usage",
    "asset_location": "asset_location",
    "subtype": "subtype",
}

NON_LIFE_SUBTYPE_KEYWORDS = {
    "personal accident": ["accident", "injury", "意外"],
    "personal liability": ["liability", "責任", "home liability", "家居責任"],
    "domestic worker insurance": ["domestic worker", "helper", "maid", "外傭", "工人"],
    "domestic helper insurance": ["domestic worker", "helper", "maid", "外傭", "工人"],
    "golf insurance": ["golf", "高爾夫"],
}

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "plan", "insurance",
    "policy", "coverage", "benefit", "benefits", "want", "need", "main", "more",
    "than", "into", "your", "their", "them", "have", "will", "aged", "years",
    "hong", "kong", "macau", "china",
}


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_key(value: str) -> str:
    return normalize_header(value).replace("__", "_")


def canonicalize_domain(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = normalize_text(raw).casefold()
    cleaned = cleaned.replace("_", " ").replace("-", " ")
    return DOMAIN_ALIASES.get(cleaned)


def canonicalize_facts(facts: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in (facts or {}).items():
        canonical_key = FACT_KEY_ALIASES.get(normalize_key(key))
        if not canonical_key:
            continue
        if isinstance(value, str):
            value = normalize_text(value)
        result[canonical_key] = value
    if "coverage_context" in result:
        ctx = normalize_text(result["coverage_context"]).casefold()
        if any(token in ctx for token in ("group", "employee", "company", "employer", "staff")):
            result["coverage_context"] = "employee_group"
        elif ctx:
            result["coverage_context"] = "individual"
    if "residence_location" in result:
        result["residence_location"] = canonicalize_location(str(result["residence_location"]))
    if "asset_location" in result:
        result["asset_location"] = canonicalize_location(str(result["asset_location"]))
    if "location_of_funds" in result:
        result["location_of_funds"] = canonicalize_location(str(result["location_of_funds"]))
    return result


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_catalog_rows(
    paths: list[Path] | None = None,
    repository: CatalogRepository | None = None,
) -> list[dict[str, Any]]:
    active_repository = repository
    if active_repository is None:
        active_repository = CsvCatalogRepository(paths or DEFAULT_CATALOGS) if paths else get_default_catalog_repository()
    return active_repository.get_rows()


def get_domain_config(domain: str) -> dict[str, Any]:
    return DOMAIN_CONFIG[domain]


def _has_value(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def activation_missing_fields(domain: str, facts: dict[str, Any]) -> list[dict[str, str]]:
    config = get_domain_config(domain)
    questions = config["questions"]
    present = {field for field in config["required_fields"] if _has_value(facts.get(field))}
    blocking: list[str] = []

    for field in config.get("activation_required_fields", []):
        if field not in present:
            blocking.append(field)

    min_fields = min(len(config["required_fields"]), int(config.get("activation_min_fields", 2)))
    if len(present) < min_fields:
        for field in config["required_fields"]:
            if field in present or field in blocking:
                continue
            blocking.append(field)
            if len(present) + len(blocking) >= min_fields:
                break

    return [{"field": field, "question": questions[field]} for field in blocking]


def missing_fields(domain: str, facts: dict[str, Any]) -> list[dict[str, str]]:
    config = get_domain_config(domain)
    questions = config["questions"]
    missing = []
    for field in config["required_fields"]:
        if not _has_value(facts.get(field)):
            missing.append({
                "field": field,
                "question": questions[field],
            })
    return missing


def canonicalize_location(value: str) -> str:
    text = normalize_text(value).casefold()
    if any(token in text for token in ("hong kong", "hk", "香港")):
        return "hong kong"
    if any(token in text for token in ("macau", "macao", "澳門", "澳门")):
        return "macau"
    if any(token in text for token in ("mainland", "china", "中國", "中国")):
        return "china"
    return text


def parse_age(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d{1,3}", str(value))
    return int(match.group()) if match else None


def extract_age_range(text: str) -> tuple[int, int] | None:
    lowered = text.casefold()
    numbers = [int(item) for item in re.findall(r"\d{1,3}", lowered)]
    if not numbers:
        return None
    if "days" in lowered:
        return (0, max(numbers))
    if len(numbers) == 1:
        return (0, numbers[0])
    return (min(numbers[0], numbers[-1]), max(numbers[0], numbers[-1]))


def collect_text(row: dict[str, Any]) -> str:
    fields = [
        "plan_name",
        "provider_company",
        "plan_category",
        "coverage_description",
        "pricing",
        "age",
        "customer_requirement",
        "price_structure",
        "additional_informations",
    ]
    return " ".join(normalize_text(row.get(field, "")) for field in fields).casefold()


def token_set(value: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9$]+", value.casefold())
        if len(token) >= 3 and token not in STOPWORDS
    }
    return tokens


def location_score(location: str | None, text: str, reasons: list[str]) -> int:
    if not location:
        return 0
    score = 0
    if location == "macau":
        if "macau-only" in text:
            reasons.append("location fits Macau-only wording")
            score += 3
        elif "macau" in text:
            reasons.append("location mentions Macau")
            score += 2
        if "hong kong only" in text or "purchase in hong kong" in text:
            reasons.append("brochure wording looks Hong Kong-specific")
            score -= 2
    elif location == "hong kong":
        if "hong kong" in text or "hkid" in text or "hong kong sar" in text:
            reasons.append("location mentions Hong Kong eligibility")
            score += 2
        if "macau-only" in text:
            reasons.append("brochure wording looks Macau-only")
            score -= 4
    elif location == "china":
        if "hong kong" in text or "macau" in text:
            reasons.append("product wording appears region-specific outside mainland")
            score -= 1
    return score


def coverage_context_score(context: str | None, text: str, reasons: list[str]) -> int:
    if not context:
        return 0
    groupish = any(token in text for token in ("employee", "employer", "company", "group", "staff", "dependant"))
    if context == "employee_group":
        if groupish:
            reasons.append("group or employer wording matches requested context")
            return 3
        reasons.append("row looks more individual than employer/group")
        return -1
    if groupish:
        reasons.append("row looks employer/group oriented while user wants individual cover")
        return -3
    reasons.append("row looks suitable for individual cover")
    return 2


def subtype_score(subtype: str | None, row: dict[str, Any], text: str, reasons: list[str]) -> int:
    if not subtype:
        return 0
    lowered = subtype.casefold()
    best = 0
    for category, keywords in NON_LIFE_SUBTYPE_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            if row.get("plan_category", "").casefold() == category:
                reasons.append(f"matches requested non-life subtype: {category}")
                return 4
            if any(keyword in text for keyword in keywords):
                best = max(best, 2)
    if best:
        reasons.append("text partially matches requested non-life subtype")
        return best
    reasons.append("subtype does not align strongly with this row")
    return -2


def amount_score(amount: str | None, text: str, reasons: list[str]) -> int:
    if not amount:
        return 0
    tokens = token_set(amount)
    if any(token in text for token in tokens) or "sum assured" in text:
        reasons.append("coverage amount cues appear in the row details")
        return 1
    return 0


def free_text_overlap(facts: dict[str, Any], text: str, reasons: list[str]) -> int:
    fields = [
        "health_conditions",
        "family_structure",
        "income_role",
        "wealth_goals",
        "growth_expectations",
        "asset_details",
        "asset_usage",
        "beneficiaries",
    ]
    tokens: set[str] = set()
    for field in fields:
        value = facts.get(field)
        if value:
            tokens.update(token_set(str(value)))
    if not tokens:
        return 0
    overlap = len([token for token in tokens if token in text])
    if overlap:
        reasons.append("row wording overlaps with the stated need")
    return min(overlap, 3)


def score_row(domain: str, row: dict[str, Any], facts: dict[str, Any]) -> tuple[int, list[str]]:
    text = collect_text(row)
    reasons: list[str] = []
    score = 1

    age = parse_age(facts.get("age"))
    age_range = extract_age_range(row.get("age", ""))
    if age is not None and age_range is not None:
        min_age, max_age = age_range
        if min_age <= age <= max_age:
            reasons.append(f"age fits row range {min_age}-{max_age}")
            score += 4
        else:
            reasons.append(f"age appears outside row range {min_age}-{max_age}")
            score -= 5

    location = (
        facts.get("residence_location")
        or facts.get("asset_location")
        or facts.get("location_of_funds")
    )
    score += location_score(location, text, reasons)
    score += coverage_context_score(facts.get("coverage_context"), text, reasons)

    if domain == "general_protection_non_life":
        score += subtype_score(facts.get("subtype"), row, text, reasons)

    score += amount_score(facts.get("desired_coverage_amount") or facts.get("desired_payout"), text, reasons)
    score += free_text_overlap(facts, text, reasons)

    if row.get("product_brochure_route"):
        score += 1
        reasons.append("brochure URL available for follow-up research")

    return score, reasons


def candidate_from_row(row: dict[str, Any], score: int, score_reasons: list[str]) -> dict[str, Any]:
    return {
        "plan_id": row.get("plan_id") or row.get("url") or row.get("plan_name"),
        "plan_name": row.get("plan_name", ""),
        "provider": row.get("provider_company", ""),
        "category": row.get("plan_category", ""),
        "url": row.get("url", ""),
        "brochure_url": row.get("product_brochure_route", ""),
        "coverage_description": row.get("coverage_description", ""),
        "pricing": row.get("pricing", ""),
        "age_text": row.get("age", ""),
        "customer_requirement": row.get("customer_requirement", ""),
        "price_structure": row.get("price_structure", ""),
        "additional_info": row.get("additional_informations", ""),
        "source_file": row.get("source_file", ""),
        "score": score,
        "score_reasons": score_reasons,
    }


def rank_products(
    domain: str,
    facts: dict[str, Any] | None,
    catalog_paths: list[Path] | None = None,
    limit: int = 3,
    repository: CatalogRepository | None = None,
) -> dict[str, Any]:
    canonical_domain = canonicalize_domain(domain)
    if not canonical_domain:
        raise ValueError(f"Unsupported domain: {domain}")

    facts_used = canonicalize_facts(facts)
    config = get_domain_config(canonical_domain)
    activation_missing = activation_missing_fields(canonical_domain, facts_used)
    remaining = missing_fields(canonical_domain, facts_used)
    blocking_fields = {item["field"] for item in activation_missing}
    result = {
        "domain": canonical_domain,
        "domain_display": config["display"],
        "mapped_categories": list(config["categories"]),
        "missing_fields": activation_missing,
        "remaining_fields": [item for item in remaining if item["field"] not in blocking_fields],
        "facts_used": facts_used,
        "candidates": [],
    }
    if activation_missing:
        return result

    try:
        rows = load_catalog_rows(catalog_paths, repository=repository)
    except CatalogUnavailableError as exc:
        result["catalog_unavailable"] = True
        result["catalog_error"] = str(exc)
        return result
    shortlisted = [row for row in rows if row.get("plan_category") in config["categories"]]

    scored = []
    for row in shortlisted:
        score, reasons = score_row(canonical_domain, row, facts_used)
        scored.append(candidate_from_row(row, score, reasons))

    scored.sort(key=lambda item: (item["score"], item["plan_name"]), reverse=True)
    result["candidates"] = [item for item in scored if item["score"] >= -1][:limit]
    return result
