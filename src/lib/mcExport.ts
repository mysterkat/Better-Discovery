type JsonObject = Record<string, unknown>;

const PHASES = [
  ["phase1", "Phase 1 - Challenge"],
  ["phase2", "Phase 2 - Verification"],
  ["funded", "Funded Account"],
  ["longterm", "Overall"],
] as const;

function escapeHtml(value: unknown): string {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function displayValue(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "number") {
    return Number.isFinite(value) ? value.toLocaleString("en-US", { maximumFractionDigits: 6 }) : String(value);
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function scalarRows(value: unknown, prefix = "", depth = 0): Array<[string, unknown]> {
  if (value === null || typeof value !== "object") return [[prefix || "value", value]];
  if (Array.isArray(value)) return [[prefix || "array", `Array (${value.length} items)`]];
  if (depth >= 4) return [[prefix || "object", "Nested object"]];

  const rows: Array<[string, unknown]> = [];
  for (const [key, child] of Object.entries(value as JsonObject)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (Array.isArray(child)) {
      rows.push([path, `Array (${child.length} items)`]);
    } else if (child !== null && typeof child === "object") {
      rows.push(...scalarRows(child, path, depth + 1));
    } else {
      rows.push([path, child]);
    }
  }
  return rows;
}

function dimensions(value: unknown): string {
  if (!Array.isArray(value)) return "";
  if (value.length === 0) return "0";
  return Array.isArray(value[0]) ? `${value.length} x ${(value[0] as unknown[]).length}` : String(value.length);
}

function inventoryRows(value: unknown, prefix = ""): Array<[string, string]> {
  if (value === null || typeof value !== "object") return [];
  const rows: Array<[string, string]> = [];
  for (const [key, child] of Object.entries(value as JsonObject)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (Array.isArray(child)) {
      rows.push([path, `array [${dimensions(child)}]`]);
    } else if (child !== null && typeof child === "object") {
      rows.push(...inventoryRows(child, path));
    }
  }
  return rows;
}

function table(rows: Array<[string, unknown]>, emptyMessage: string): string {
  if (rows.length === 0) return `<p class="muted">${escapeHtml(emptyMessage)}</p>`;
  return `<table><thead><tr><th>Metric / data path</th><th>Value</th></tr></thead><tbody>${rows
    .map(([key, value]) => `<tr><td><code>${escapeHtml(key)}</code></td><td>${escapeHtml(displayValue(value))}</td></tr>`)
    .join("")}</tbody></table>`;
}

function phaseSection(result: JsonObject, id: string, label: string): string {
  const phase = result[id];
  if (phase === null || typeof phase !== "object") {
    return `<section><h2>${escapeHtml(label)}</h2><p class="muted">No result payload was returned for this tab.</p></section>`;
  }
  const rows = scalarRows(phase).filter(([, value]) => !String(value).startsWith("Array ("));
  const inventory = inventoryRows(phase);
  return `<section id="${escapeHtml(id)}">
    <h2>${escapeHtml(label)}</h2>
    ${table(rows, "No scalar metrics returned.")}
    <details><summary>Array and simulation-data inventory</summary>${table(inventory, "No array data returned.")}</details>
  </section>`;
}

export function buildMonteCarloHtml(jobId: string, result: JsonObject): string {
  const generatedAt = new Date().toISOString();
  const payload = JSON.stringify({
    schema: "better-discovery-monte-carlo-export-v1",
    job_id: jobId,
    generated_at: generatedAt,
    result,
  }).replaceAll("<", "\\u003c");
  const otherRows = scalarRows(result)
    .filter(([key, value]) => !PHASES.some(([id]) => key === id || key.startsWith(`${id}.`)) && !String(value).startsWith("Array ("));

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BETTER DISCOVERY Monte Carlo ${escapeHtml(jobId.slice(0, 8))}</title>
<style>
  :root{color-scheme:light;--ink:#172033;--muted:#657084;--line:#d8dde6;--head:#eef2f7;--accent:#245f9e}
  *{box-sizing:border-box}body{margin:0;background:#fff;color:var(--ink);font:14px/1.45 Arial,sans-serif}
  main{max-width:1120px;margin:0 auto;padding:36px 28px 72px}header{border-bottom:2px solid var(--accent);padding-bottom:18px;margin-bottom:28px}
  h1{font-size:28px;margin:0 0 8px}h2{font-size:20px;color:var(--accent);margin:32px 0 12px}p{margin:6px 0}.muted{color:var(--muted)}
  .meta{display:grid;grid-template-columns:160px 1fr;gap:5px 16px}.notice{border:1px solid var(--line);background:#f7f9fc;padding:12px 14px;margin:18px 0}
  table{border-collapse:collapse;width:100%;margin:10px 0 16px;table-layout:fixed}th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
  th{background:var(--head);font-weight:700}th:first-child,td:first-child{width:46%}code{font:12px Consolas,monospace}details{margin:10px 0}summary{cursor:pointer;font-weight:700;color:var(--accent)}
  @media print{main{max-width:none;padding:18mm}section{break-before:page}header{break-after:avoid}details{display:block}summary{display:none}}
</style></head><body><main>
<header><h1>BETTER DISCOVERY Monte Carlo Report</h1><div class="meta"><strong>Job ID</strong><span>${escapeHtml(jobId)}</span><strong>Generated</strong><span>${escapeHtml(generatedAt)}</span><strong>Coverage</strong><span>All four dashboard tabs and complete raw result payload</span></div></header>
<div class="notice"><strong>Analysis note:</strong> This HTML contains the complete, untruncated result object in <code>script#mc-result-data</code>. The tables below are readable projections; array dimensions are listed separately so large simulation paths do not overwhelm the report.</div>
<section><h2>Global Results and Verdicts</h2>${table(otherRows, "No global metrics returned.")}</section>
${PHASES.map(([id, label]) => phaseSection(result, id, label)).join("\n")}
<section><h2>Complete Payload Inventory</h2>${table(inventoryRows(result), "No array data returned.")}</section>
<script id="mc-result-data" type="application/json">${payload}</script>
</main></body></html>`;
}

export function downloadMonteCarloHtml(jobId: string, result: JsonObject): string {
  const html = buildMonteCarloHtml(jobId, result);
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const date = new Date().toISOString().slice(0, 10);
  const filename = `BETTER_DISCOVERY_MC_${jobId.slice(0, 8)}_${date}.html`;
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  return filename;
}
