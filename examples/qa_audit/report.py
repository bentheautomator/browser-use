"""Render beautiful Markdown + JSON audit report.

Inputs:  tmp/qa-pages.json, tmp/link-probe.json, tmp/spa-route-probe.json,
         tmp/audit-findings-full.json
Outputs: tmp/qa-report.md, tmp/qa-report.json (combined)
"""
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path("/Users/automator/git/bentheautomator/browser-use")
QA = REPO / "tmp" / "qa-pages.json"
LINK = REPO / "tmp" / "link-probe.json"
SPA = REPO / "tmp" / "spa-route-probe.json"
FIND = REPO / "tmp" / "audit-findings-full.json"
MD_OUT = REPO / "tmp" / "qa-report.md"
JSON_OUT = REPO / "tmp" / "qa-report.json"

SEV_BADGE = {"issue": "🔴 **ISSUE**", "warn": "🟡 warn", "info": "🟢 info"}
SEV_EMOJI = {"issue": "🔴", "warn": "🟡", "info": "🟢"}


def short(url: str) -> str:
	return url.replace("http://localhost:5234", "")


def render_md(qa, link, spa, find) -> str:
	totals = Counter(f["sev"] for f in find)
	pages = [p for p in qa if "error" not in p]
	now = datetime.now().strftime("%Y-%m-%d %H:%M")
	out = []
	out.append(f"# QA Audit Report")
	out.append(f"_{now} — FCO Command Center (localhost:5234) — On Plane Consulting tenant_\n")
	# executive summary
	out.append("## Executive Summary\n")
	out.append(f"- **{len(pages)}** pages audited")
	out.append(f"- **{len(find)}** total findings across **12** categories")
	out.append(
		f"- Severity: {SEV_EMOJI['issue']} **{totals.get('issue', 0)} issues**, "
		f"{SEV_EMOJI['warn']} **{totals.get('warn', 0)} warnings**, "
		f"{SEV_EMOJI['info']} **{totals.get('info', 0)} info**"
	)
	# top issues
	issues = [f for f in find if f["sev"] == "issue"]
	if issues:
		out.append(f"\n### 🔴 Critical Issues ({len(issues)})\n")
		for f in issues:
			out.append(f"- **[Cat {f['cat']:2d} · {f['category']}]** `{short(f['url'])}` — {f['msg']}")
	# pages overview table
	out.append("\n## Pages Overview\n")
	out.append("| Page | HTTP | Interactives | Buttons | Links | Console err | Exceptions | Mobile OK | SPA-404 |")
	out.append("|---|---|---|---|---|---|---|---|---|")
	spa_map = {r["href"]: r for r in spa}
	for p in pages:
		spa_rec = spa_map.get(p["target_url"], {})
		mobile_ok = "✓" if (p.get("mobile") or {}).get("viewportMeta") and not (p.get("mobile") or {}).get("hasOverflowX") else "✗"
		spa404 = "❌" if spa_rec.get("spa_404") else "✓"
		console_err = sum(1 for m in p.get("console_msgs", []) if m.get("level") == "error")
		out.append(
			f"| `{short(p['target_url'])}` | {p.get('main_status', '?')} | "
			f"{p.get('interactive_count', 0)} | {len(p.get('buttons', []))} | "
			f"{len(p.get('all_links', []))} | {console_err} | "
			f"{len(p.get('exceptions', []))} | {mobile_ok} | {spa404} |"
		)
	# category rollup
	out.append("\n## Category Rollup\n")
	out.append("| # | Category | 🔴 | 🟡 | 🟢 |")
	out.append("|---|---|---:|---:|---:|")
	by_cat = defaultdict(lambda: Counter())
	cat_names = {}
	for f in find:
		by_cat[f["cat"]][f["sev"]] += 1
		cat_names[f["cat"]] = f["category"]
	for num in sorted(by_cat.keys()):
		c = by_cat[num]
		out.append(f"| {num} | {cat_names[num]} | {c.get('issue', 0)} | {c.get('warn', 0)} | {c.get('info', 0)} |")
	# per-page deep dive
	out.append("\n## Per-Page Detail\n")
	for p in pages:
		url_short = short(p["target_url"])
		out.append(f"\n### `{url_short}`\n")
		out.append(f"- **HTTP main:** {p.get('main_status')}")
		out.append(f"- **Landed URL:** `{p.get('landed_url')}`")
		out.append(f"- **Webmap page_type:** `{p.get('page_type')!r}`  purpose: `{p.get('page_purpose')!r}`")
		out.append(f"- **Interactives:** {p.get('interactive_count', 0)} ({'AX fallback' if p.get('enriched_via_ax_tree') else 'webmap native'})")
		out.append(f"- **Buttons:** {len(p.get('buttons', []))}  ·  **Links:** {len(p.get('all_links', []))}  ·  **Forms:** {p.get('forms', 0)}  ·  **Inputs:** {p.get('inputs', 0)}")
		out.append(f"- **Landmarks:** {p.get('regions', [])}")
		hs = p.get("headings", [])
		if hs:
			ht = "  \n  ".join(f"`<h{h['level']}> {h['text'][:60]}`" for h in hs)
			out.append(f"- **Headings ({len(hs)}):**  \n  {ht}")
		m = p.get("mobile") or {}
		out.append(f"- **Mobile @ 375px:** viewport meta = `{m.get('viewportMeta')!r}`, overflowX = `{m.get('hasOverflowX')}`")
		# findings for this page
		page_finds = [f for f in find if f["url"] == p["target_url"]]
		out.append(f"\n#### Findings ({len(page_finds)})\n")
		out.append("| Cat | Category | Sev | Message |")
		out.append("|---|---|---|---|")
		for f in sorted(page_finds, key=lambda x: (x["cat"], {"issue": 0, "warn": 1, "info": 2}[x["sev"]])):
			msg = f["msg"].replace("|", "\\|")
			out.append(f"| {f['cat']} | {f['category']} | {SEV_EMOJI[f['sev']]} {f['sev']} | {msg} |")
	# link probe summary
	out.append("\n## Link Probe Summary\n")
	statuses = Counter()
	for r in link:
		if "status" in r:
			statuses[f"{r['status'] // 100}xx"] += 1
		elif "error" in r:
			statuses["error"] += 1
	out.append(f"- {len(link)} unique hrefs probed via HEAD")
	out.append(f"- Rollup: {dict(statuses)}")
	# SPA route probe
	out.append("\n## SPA Route Probe Summary\n")
	spa_404 = [r for r in spa if r.get("spa_404")]
	out.append(f"- {len(spa)} same-origin routes rendered via authenticated browser")
	out.append(f"- SPA-404 detected: {len(spa_404)}")
	for r in spa_404:
		out.append(f"  - **`{short(r['href'])}`** — title=`{r.get('title')!r}` headings=`{[h['text'] for h in r.get('headings', [])][:4]}`")
	# methodology
	out.append("\n## Methodology\n")
	out.append("Auth state captured once via `AuthCaptureSession` (headful chromium → magic-link login → storage_state persisted).")
	out.append("Per-page scan via `AuthScanSession` (headless chromium reusing storage_state) with:")
	out.append("- CDP `Network.responseReceived` / `Network.loadingFailed` → status codes + failed requests")
	out.append("- CDP `Runtime.consoleAPICalled` / `Runtime.exceptionThrown` → console errors + JS exceptions")
	out.append("- `webmap scan ... --stdin` → semantic page structure (buttons, links, forms, regions)")
	out.append("- CDP `Accessibility.getFullAXTree` → AX-tree fallback when webmap returns 0 interactives")
	out.append("- CDP `Emulation.setDeviceMetricsOverride` (375×812) → mobile overflow + viewport meta check")
	out.append("- httpx HEAD probe (cookies restored from storage_state) → external + internal link statuses")
	out.append("- second AuthScanSession pass navigating each same-origin link → SPA-level 404 + error-boundary detection")
	out.append("\nAudit categories synthesized from proposed UX heuristics + Nielsen 10 + WCAG 2.2 AA. See `tmp/audit_full.py` for thresholds.")
	out.append("\n---\n_Generated by browser-use webmap integration steroid stack._")
	return "\n".join(out)


def main():
	qa = json.loads(QA.read_text())
	link = json.loads(LINK.read_text())
	spa = json.loads(SPA.read_text())
	find = json.loads(FIND.read_text())
	md = render_md(qa, link, spa, find)
	MD_OUT.write_text(md)
	# combined JSON
	combined = {
		"generated_at": datetime.now().isoformat(),
		"target": "FCO Command Center @ http://localhost:5234 (on-plane-consulting tenant)",
		"pages": qa,
		"link_probe": link,
		"spa_route_probe": spa,
		"audit_findings": find,
		"rollup": {
			"pages": len([p for p in qa if "error" not in p]),
			"findings": len(find),
			"by_severity": dict(Counter(f["sev"] for f in find)),
			"by_category": {f"cat{c}": dict(Counter(f["sev"] for f in find if f["cat"] == c)) for c in range(1, 13)},
		},
	}
	JSON_OUT.write_text(json.dumps(combined, indent=2))
	print(f"wrote {MD_OUT} ({MD_OUT.stat().st_size} bytes)")
	print(f"wrote {JSON_OUT} ({JSON_OUT.stat().st_size} bytes)")


if __name__ == "__main__":
	main()
