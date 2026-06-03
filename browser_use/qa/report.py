"""
Report renderer — beautiful Markdown + combined JSON from the QA pipeline
artifacts.

`render_markdown(pages, link_probe, spa_probe, findings, *, title=...)`
returns a Markdown string with sections:
  - Executive Summary (totals + critical issues)
  - Pages Overview table
  - Category Rollup table
  - Per-page detail with per-cat finding tables
  - Link Probe Summary
  - SPA Route Probe Summary
  - Methodology

`render_combined_json(pages, link_probe, spa_probe, findings)` returns the
machine-readable dict that pairs with the Markdown.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

SEV_EMOJI: dict[str, str] = {'issue': '🔴', 'warn': '🟡', 'info': '🟢'}


def _short(url: str, base: str | None) -> str:
	if base and url.startswith(base):
		return url[len(base) :]
	return url


def render_markdown(
	pages: list[dict],
	link_probe: list[dict],
	spa_probe: list[dict],
	findings: list[dict],
	*,
	title: str = 'QA Audit Report',
	target_label: str | None = None,
	base_url: str | None = None,
) -> str:
	"""Render the audit report as a single Markdown string.

	Args:
	    pages, link_probe, spa_probe, findings: the four artifact lists
	        produced by `QAPipeline`.
	    title: top-level h1.
	    target_label: short subtitle line (e.g. "Acme Dashboard @ prod").
	    base_url: when set, URLs are shortened by stripping this prefix in
	        the rendered tables.
	"""
	totals = Counter(f['sev'] for f in findings)
	scanned = [p for p in pages if 'error' not in p]
	now = datetime.now().strftime('%Y-%m-%d %H:%M')
	out: list[str] = []
	out.append(f'# {title}')
	if target_label:
		out.append(f'_{now} — {target_label}_\n')
	else:
		out.append(f'_{now}_\n')

	out.append('## Executive Summary\n')
	out.append(f'- **{len(scanned)}** pages audited')
	out.append(f'- **{len(findings)}** total findings across **12** categories')
	out.append(
		f'- Severity: {SEV_EMOJI["issue"]} **{totals.get("issue", 0)} issues**, '
		f'{SEV_EMOJI["warn"]} **{totals.get("warn", 0)} warnings**, '
		f'{SEV_EMOJI["info"]} **{totals.get("info", 0)} info**'
	)
	issues = [f for f in findings if f['sev'] == 'issue']
	if issues:
		out.append(f'\n### 🔴 Critical Issues ({len(issues)})\n')
		for f in issues:
			out.append(f'- **[Cat {f["cat"]:2d} · {f["category"]}]** `{_short(f["url"], base_url)}` — {f["msg"]}')

	out.append('\n## Pages Overview\n')
	out.append('| Page | HTTP | Interactives | Buttons | Links | Console err | Exceptions | Mobile OK | SPA-404 |')
	out.append('|---|---|---|---|---|---|---|---|---|')
	spa_map = {r['href']: r for r in spa_probe}
	for p in scanned:
		spa_rec = spa_map.get(p['target_url'], {})
		mobile = p.get('mobile') or {}
		mobile_ok = '✓' if mobile.get('viewportMeta') and not mobile.get('hasOverflowX') else '✗'
		spa404 = '❌' if spa_rec.get('spa_404') else '✓'
		console_err = sum(1 for m in p.get('console_msgs', []) if m.get('level') == 'error')
		out.append(
			f'| `{_short(p["target_url"], base_url)}` | {p.get("main_status", "?")} | '
			f'{p.get("interactive_count", 0)} | {len(p.get("buttons", []))} | '
			f'{len(p.get("all_links", []))} | {console_err} | '
			f'{len(p.get("exceptions", []))} | {mobile_ok} | {spa404} |'
		)

	out.append('\n## Category Rollup\n')
	out.append('| # | Category | 🔴 | 🟡 | 🟢 |')
	out.append('|---|---|---:|---:|---:|')
	by_cat: dict[int, Counter] = defaultdict(Counter)
	cat_names: dict[int, str] = {}
	for f in findings:
		by_cat[f['cat']][f['sev']] += 1
		cat_names[f['cat']] = f['category']
	for num in sorted(by_cat.keys()):
		c = by_cat[num]
		out.append(f'| {num} | {cat_names[num]} | {c.get("issue", 0)} | {c.get("warn", 0)} | {c.get("info", 0)} |')

	out.append('\n## Per-Page Detail\n')
	for p in scanned:
		url_short = _short(p['target_url'], base_url)
		out.append(f'\n### `{url_short}`\n')
		out.append(f'- **HTTP main:** {p.get("main_status")}')
		out.append(f'- **Landed URL:** `{p.get("landed_url")}`')
		out.append(f'- **Webmap page_type:** `{p.get("page_type")!r}`  purpose: `{p.get("page_purpose")!r}`')
		ax_note = 'AX fallback' if p.get('enriched_via_ax_tree') else 'webmap native'
		out.append(f'- **Interactives:** {p.get("interactive_count", 0)} ({ax_note})')
		out.append(
			f'- **Buttons:** {len(p.get("buttons", []))}  ·  **Links:** {len(p.get("all_links", []))}  ·  '
			f'**Forms:** {p.get("forms", 0)}  ·  **Inputs:** {p.get("inputs", 0)}'
		)
		out.append(f'- **Landmarks:** {p.get("regions", [])}')
		hs = p.get('headings', [])
		if hs:
			ht = '  \n  '.join(f'`<h{h["level"]}> {h["text"][:60]}`' for h in hs)
			out.append(f'- **Headings ({len(hs)}):**  \n  {ht}')
		mobile = p.get('mobile') or {}
		out.append(
			f'- **Mobile @ 375px:** viewport meta = `{mobile.get("viewportMeta")!r}`, overflowX = `{mobile.get("hasOverflowX")}`'
		)
		page_finds = [f for f in findings if f['url'] == p['target_url']]
		out.append(f'\n#### Findings ({len(page_finds)})\n')
		out.append('| Cat | Category | Sev | Message |')
		out.append('|---|---|---|---|')
		sev_order = {'issue': 0, 'warn': 1, 'info': 2}
		for f in sorted(page_finds, key=lambda x: (x['cat'], sev_order[x['sev']])):
			msg = f['msg'].replace('|', '\\|')
			out.append(f'| {f["cat"]} | {f["category"]} | {SEV_EMOJI[f["sev"]]} {f["sev"]} | {msg} |')

	out.append('\n## Link Probe Summary\n')
	statuses: Counter = Counter()
	for r in link_probe:
		if 'status' in r:
			statuses[f'{r["status"] // 100}xx'] += 1
		elif 'error' in r:
			statuses['error'] += 1
	out.append(f'- {len(link_probe)} unique hrefs probed via HEAD')
	out.append(f'- Rollup: {dict(statuses)}')

	out.append('\n## SPA Route Probe Summary\n')
	spa_404_pages = [r for r in spa_probe if r.get('spa_404')]
	out.append(f'- {len(spa_probe)} same-origin routes rendered via authenticated browser')
	out.append(f'- SPA-404 detected: {len(spa_404_pages)}')
	for r in spa_404_pages:
		out.append(
			f'  - **`{_short(r["href"], base_url)}`** — title=`{r.get("title")!r}` '
			f'headings=`{[h["text"] for h in r.get("headings", [])][:4]}`'
		)

	out.append('\n## Methodology\n')
	out.append('Generated by `browser_use.qa.QAPipeline` — see module docstring for the full call shape.')
	return '\n'.join(out)


def render_combined_json(
	pages: list[dict],
	link_probe: list[dict],
	spa_probe: list[dict],
	findings: list[dict],
	*,
	target_label: str | None = None,
) -> dict:
	"""Build the combined JSON shape that pairs with the Markdown report."""
	totals = Counter(f['sev'] for f in findings)
	by_cat: dict[str, dict] = {}
	for c in range(1, 13):
		by_cat[f'cat{c}'] = dict(Counter(f['sev'] for f in findings if f['cat'] == c))
	return {
		'generated_at': datetime.now().isoformat(),
		'target': target_label,
		'pages': pages,
		'link_probe': link_probe,
		'spa_route_probe': spa_probe,
		'audit_findings': findings,
		'rollup': {
			'pages': len([p for p in pages if 'error' not in p]),
			'findings': len(findings),
			'by_severity': dict(totals),
			'by_category': by_cat,
		},
	}
