---
name: tavily-search
description: AI-optimized web search and URL extraction through Tavily. Use when brochure or web research is needed after a local shortlist already exists.
homepage: https://tavily.com
metadata: {"nanobot":{"emoji":"🔍","requires":{"bins":["node"],"env":["TAVILY_API_KEY"]},"primaryEnv":"TAVILY_API_KEY"}}
---

# Tavily Search

Use this skill for focused web research and page extraction through the Tavily API.

## When to use

- You already know the product or URL you need to research.
- You need brochure extraction or narrow web verification.
- Do not use this as open-ended research before the local shortlist exists.

## Search

```bash
node {baseDir}/scripts/search.mjs "query"
node {baseDir}/scripts/search.mjs "query" -n 10
node {baseDir}/scripts/search.mjs "query" --deep
node {baseDir}/scripts/search.mjs "query" --topic news --days 30
node {baseDir}/scripts/search.mjs "query" --json
```

## Extract content from URL

```bash
node {baseDir}/scripts/extract.mjs "https://example.com/article"
node {baseDir}/scripts/extract.mjs "https://example.com/article" --json
```

## Notes

- Requires `TAVILY_API_KEY`.
- Prefer `extract.mjs` when you already have a brochure URL.
- Prefer `search.mjs --deep` only as a fallback when direct extraction is thin or fails.
- Use `--json` when another script needs structured output.
