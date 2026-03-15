---
name: insurance-product-advisor
description: Local insurance product lookup and Tavily brochure research for matched WhatsApp group participants. Use when the participant asks for product information, comparison, suitability, or recommendations.
metadata: {"nanobot":{"emoji":"🧾","requires":{"bins":["python3"]}}}
---

# Insurance Product Advisor

Use this skill for matched WhatsApp insurance conversations whenever the runtime context says the session is in the insurance flow.

## Trigger

Apply this skill based on the runtime insurance flow state:

- `generic` mode:
  - answer naturally
  - steer the participant one step closer to qualification
  - do not run local shortlist or brochure research yet
- `skill` mode:
  - stop generic product advice
  - ask only the next required fact, or run the product workflow immediately if the minimum facts are complete
  - if the participant asks for a direct recommendation, do not stay in generic advisor mode

The session can enter `skill` mode in 2 ways:

- after the normal 2 generic insurance replies
- immediately, if the current conversation already contains the product domain plus 2 domain-relevant facts

## Workflow

1. Reuse facts already present in the chat history. Do not ask for the same fact twice.
2. If runtime mode is `generic`, answer the user normally and ask only one light steering question.
3. If runtime mode is `skill`, first check whether the product domain is known.
4. If the domain is unknown, ask the first-layer domain question only.
5. If the domain is known, check whether the conversation already contains at least 2 domain-relevant facts.
6. If the domain plus 2 useful facts are already present, run the shortlist immediately. Do not block on collecting every remaining field first.
7. If fewer than 2 useful facts are present, ask only the next missing fact in one short sentence.
8. If the shortlist result includes `remaining_fields`, treat them as refinement questions, not blockers for the first recommendation pass.
9. Save the collected facts to a temporary JSON file in the workspace.
10. Run `scripts/find_products.py` to get `missing_fields`, `remaining_fields`, or shortlisted candidates.
11. If `missing_fields` is non-empty, ask only those questions and stop.
12. If candidates exist, save them to a temporary JSON file and run `scripts/research_products.py`.
13. Reply in Traditional Chinese with a compact top-3 comparison grounded in the local CSV data and Tavily brochure research.

## First-Layer Domain Menu

Ask the participant which direction they want first when the domain is not yet clear:

- `Dental`
- `Health / Medical`
- `Critical Illness`
- `Life Protection`
- `Savings / Retirement`
- `General Protection / Non-Life`

## Minimum Facts By Domain

- `Dental`
  - `age`
  - `residence_location`
  - `coverage_context` (`individual` or `employee_group`)
- `Health / Medical`
  - `age`
  - `health_conditions`
  - `residence_location`
- `Critical Illness`
  - `age`
  - `health_conditions`
  - `desired_coverage_amount`
- `Life Protection`
  - `age`
  - `health_conditions`
  - `family_structure`
  - `income_role`
  - `desired_payout`
  - `beneficiaries`
- `Savings / Retirement`
  - `location_of_funds`
  - `investment_amount`
  - `wealth_goals`
  - `growth_expectations`
- `General Protection / Non-Life`
  - `subtype`
  - `asset_details`
  - `asset_usage`
  - `asset_location`

## Helper Scripts

The helper scripts live in the same skill directory under `scripts/`.

### 1. Local shortlist

Write facts JSON to a temp file, then run:

```bash
python3 <skill-dir>/scripts/find_products.py --domain "Dental" --facts-file /tmp/facts.json
```

Expected output fields:

- `domain`
- `domain_display`
- `mapped_categories`
- `missing_fields`
- `remaining_fields`
- `facts_used`
- `candidates`

Each candidate includes:

- `plan_id`
- `plan_name`
- `provider`
- `category`
- `brochure_url`
- `score`
- `score_reasons`
- local CSV fact fields such as pricing, coverage description, age, requirements, and additional info

### 2. Brochure research

After shortlist selection, write the `candidates` array to a temp file and run:

```bash
python3 <skill-dir>/scripts/research_products.py --candidates-file /tmp/candidates.json
```

This script:

- uses direct brochure extraction first
- falls back to focused Tavily deep search if extraction is thin
- returns concise brochure-backed notes per product
- falls back to local CSV facts only if Tavily is unavailable or fails

## Reply Rules

- Keep the conversation professional, calm, and natural.
- Do not expose the internal workflow, script names, or raw JSON in the final reply.
- Do not invent premiums, underwriting decisions, guarantees, or policy facts.
- If the catalog fit is weak, say so clearly.
- If brochure verification is missing, say the recommendation is based on the local product file and the brochure details could not be fully verified.
- Do not use bullet points unless the user explicitly asks for a list.
- Avoid perfect list-like structure. Prefer 2-4 short paragraphs.
- Do not sound humble, flattering, or pushy.
- If `remaining_fields` exists after a shortlist, give the recommendation first and only then ask one short refinement question if it would materially improve the next step.

## Final Reply Shape

- brief recap of the participant’s need in one sentence
- 2-4 short natural paragraphs covering the top products, their fit, and key caveats
- one short comparison sentence across the options
- one focused follow-up question only if a decision-critical gap still remains
