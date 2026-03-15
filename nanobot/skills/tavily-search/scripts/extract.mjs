#!/usr/bin/env node

function usage() {
  console.error(`Usage: extract.mjs "url1" ["url2" ...] [--json]`);
  process.exit(2);
}

const args = process.argv.slice(2);
if (args.length === 0 || args[0] === "-h" || args[0] === "--help") usage();

let asJson = false;
const urls = [];
for (const arg of args) {
  if (arg === "--json") {
    asJson = true;
    continue;
  }
  if (!arg.startsWith("-")) {
    urls.push(arg);
  }
}

if (urls.length === 0) {
  console.error("No URLs provided");
  usage();
}

const apiKey = (process.env.TAVILY_API_KEY ?? "").trim();
if (!apiKey) {
  console.error("Missing TAVILY_API_KEY");
  process.exit(1);
}

const resp = await fetch("https://api.tavily.com/extract", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    api_key: apiKey,
    urls: urls,
  }),
});

if (!resp.ok) {
  const text = await resp.text().catch(() => "");
  throw new Error(`Tavily Extract failed (${resp.status}): ${text}`);
}

const data = await resp.json();
const results = (data.results ?? []).map((r) => ({
  url: String(r?.url ?? "").trim(),
  raw_content: String(r?.raw_content ?? "").trim(),
}));
const failed = (data.failed_results ?? []).map((f) => ({
  url: String(f?.url ?? "").trim(),
  error: String(f?.error ?? "").trim(),
}));

if (asJson) {
  console.log(JSON.stringify({
    results,
    failed_results: failed,
  }, null, 2));
  process.exit(0);
}

for (const r of results) {
  console.log(`# ${r.url}\n`);
  console.log(r.raw_content || "(no content extracted)");
  console.log("\n---\n");
}

if (failed.length > 0) {
  console.log("## Failed URLs\n");
  for (const f of failed) {
    console.log(`- ${f.url}: ${f.error}`);
  }
}
