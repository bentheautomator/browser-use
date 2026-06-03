"""12-category extended audit driver.

Consumes:
  tmp/qa-pages.json         (per-page deep scan)
  tmp/link-probe.json       (HTTP HEAD probe of all hrefs)
  tmp/spa-route-probe.json  (SPA-level render probe of same-origin links)

Emits:
  tmp/audit-findings-full.json  (machine-readable, one record per finding)
  prints rollup to stdout

Categories (synthesized from proposed UX + Nielsen 10 + WCAG 2.2 AA):
   1. Landmarks & Structure
   2. Navigation Clarity
   3. Actions & Affordances
   4. Forms & Inputs
   5. A11y Name Coverage
   6. Label Hygiene
   7. Interactive Density
   8. Auth Posture / System Status
   9. Routes & Network Health         (NEW)
  10. Runtime Errors & Broken Data    (NEW)
  11. Mobile Friendliness             (NEW)
  12. Heading Order & Page Titles     (NEW)
"""
import json
import re
from collections import Counter
from pathlib import Path

REPO = Path("/Users/automator/git/bentheautomator/browser-use")
QA = REPO / "tmp" / "qa-pages.json"
LINK = REPO / "tmp" / "link-probe.json"
SPA = REPO / "tmp" / "spa-route-probe.json"
OUTPUT = REPO / "tmp" / "audit-findings-full.json"

AMBIGUOUS = {"button", "click here", "click", "more", "ok", "yes", "no", "link", "here", "read more", "...", "?", "!"}
EXPECTED_LANDMARKS = {"nav", "aside", "main", "header", "footer"}


def cat1_landmarks(p):
	regions_text = " ".join(p.get("regions", [])).lower()
	found = {lm for lm in EXPECTED_LANDMARKS if lm in regions_text}
	missing = EXPECTED_LANDMARKS - found
	if len(found) < 3:
		yield {"sev": "issue", "msg": f"only {len(found)} landmarks (found={sorted(found)})"}
	elif missing:
		yield {"sev": "warn", "msg": f"missing landmarks: {sorted(missing)}"}
	else:
		yield {"sev": "info", "msg": f"all landmarks present: {sorted(found)}"}


def cat2_nav(p):
	links = p.get("links_webmap", [])
	n = len(links)
	if n == 0:
		yield {"sev": "issue", "msg": "zero links — no navigation"}
		return
	short = [l for l in links if len(l.strip()) < 3]
	if short:
		yield {"sev": "warn", "msg": f"{len(short)} links with <3 char text"}
	if n > 50:
		yield {"sev": "warn", "msg": f"{n} links — likely too dense"}
	yield {"sev": "info", "msg": f"{n} nav links"}


def cat3_actions(p):
	buttons = p.get("buttons", [])
	if not buttons:
		yield {"sev": "warn", "msg": "zero buttons"}
		return
	amb = [b for b in buttons if b.strip().lower() in AMBIGUOUS]
	if amb:
		yield {"sev": "issue", "msg": f"{len(amb)} ambiguous button labels: {amb}"}
	yield {"sev": "info", "msg": f"{len(buttons)} buttons"}


def cat4_forms(p):
	f = p.get("forms", 0)
	i = p.get("inputs", 0)
	if f == 0 and i > 0:
		yield {"sev": "warn", "msg": f"{i} inputs but 0 <form> wrappers — no native submit/autofill semantics"}
	yield {"sev": "info", "msg": f"forms={f} inputs={i}"}


def cat5_ax(p):
	ax = p.get("ax_interactives", [])
	if not ax and not p.get("enriched_via_ax_tree"):
		yield {"sev": "info", "msg": "webmap covered everything, AX not sampled"}
		return
	if not ax:
		yield {"sev": "warn", "msg": "AX fallback ran but returned 0 nodes"}
		return
	unnamed = [n for n in ax if not n.get("name")]
	pct = round(100 * len(unnamed) / len(ax), 1)
	sev = "issue" if pct > 20 else "warn" if pct > 5 else "info"
	yield {"sev": sev, "msg": f"{len(unnamed)}/{len(ax)} AX nodes ({pct}%) have no accessible name"}


def cat6_hygiene(p):
	labels = [l.strip() for l in p.get("buttons", []) + p.get("links_webmap", []) if l.strip()]
	dupes = [k for k, v in Counter(labels).items() if v > 1]
	if dupes:
		yield {"sev": "warn", "msg": f"{len(dupes)} duplicate labels: {dupes[:5]}"}
	url_leaks = [l for l in labels if "http" in l.lower() or l.startswith("/")]
	if url_leaks:
		yield {"sev": "issue", "msg": f"{len(url_leaks)} labels look like raw URLs"}


def cat7_density(p):
	i = p.get("interactive_count", 0)
	r = len(p.get("regions", []))
	ratio = round(i / r, 1) if r else float("inf")
	sev = "warn" if ratio > 15 else "info"
	yield {"sev": sev, "msg": f"{i} interactives / {r} regions (ratio {ratio})"}


def cat8_auth(p):
	labels = " | ".join((b.lower() for b in p.get("buttons", []) + p.get("links_webmap", [])))
	has_signout = "sign out" in labels or "log out" in labels or "logout" in labels
	on_login = "/login" in (p.get("landed_url") or "")
	if on_login:
		yield {"sev": "issue", "msg": f"landed on /login: {p.get('landed_url')}"}
	elif has_signout:
		yield {"sev": "info", "msg": "Sign Out present — authenticated"}
	else:
		yield {"sev": "warn", "msg": "no Sign Out detected"}


def cat9_routes(p, link_probe_by_href: dict):
	status = p.get("main_status")
	if status is None:
		yield {"sev": "warn", "msg": "main document status not captured"}
	elif status >= 400:
		yield {"sev": "issue", "msg": f"main document HTTP {status}"}
	else:
		yield {"sev": "info", "msg": f"main document HTTP {status}"}
	bad = p.get("bad_responses", [])
	if bad:
		yield {"sev": "issue", "msg": f"{len(bad)} 4xx/5xx subresource responses: {[(b['status'], b['url'][:60]) for b in bad[:3]]}"}
	fail = p.get("failed_requests", [])
	if fail:
		yield {"sev": "warn", "msg": f"{len(fail)} failed requests: {[f['reason'] for f in fail[:3]]}"}
	# link probe summary
	page_hrefs = [(ln.get("href") or "") for ln in p.get("all_links", [])]
	page_probes = [link_probe_by_href.get(h) for h in page_hrefs if h in link_probe_by_href]
	bad_links = [r for r in page_probes if r and r.get("status", 200) >= 400]
	err_links = [r for r in page_probes if r and "error" in r]
	if bad_links:
		yield {"sev": "issue", "msg": f"{len(bad_links)} links return >=400: {[(b['status'], b['href'][:60]) for b in bad_links[:5]]}"}
	if err_links:
		yield {"sev": "warn", "msg": f"{len(err_links)} link probes errored: {[e['error'] for e in err_links[:3]]}"}


def cat10_runtime(p, spa_probe_by_url: dict):
	cm = p.get("console_msgs", [])
	errs = [m for m in cm if m.get("level") == "error"]
	warns = [m for m in cm if m.get("level") == "warning"]
	if errs:
		yield {"sev": "issue", "msg": f"{len(errs)} console errors: {[e['text'][:120] for e in errs[:3]]}"}
	if warns:
		yield {"sev": "warn", "msg": f"{len(warns)} console warnings"}
	exc = p.get("exceptions", [])
	if exc:
		yield {"sev": "issue", "msg": f"{len(exc)} JS exceptions: {[e['text'][:120] for e in exc[:3]]}"}
	bdm = p.get("broken_data_markers_found", [])
	if bdm:
		yield {"sev": "warn", "msg": f"broken-data markers in body text: {bdm}"}
	# SPA-404 check
	spa = spa_probe_by_url.get(p.get("target_url"))
	if spa and spa.get("spa_404"):
		yield {"sev": "issue", "msg": f"SPA renders 404 component (HTTP 200 but page shows 'Page Not Found')"}
	if spa and spa.get("error_boundary_markers"):
		yield {"sev": "issue", "msg": f"error boundary text visible: {spa['error_boundary_markers']}"}


def cat11_mobile(p):
	m = p.get("mobile", {}) or {}
	if not m:
		yield {"sev": "warn", "msg": "mobile pass did not run"}
		return
	if not m.get("viewportMeta"):
		yield {"sev": "issue", "msg": "missing <meta name='viewport'> — page won't scale on mobile"}
	if m.get("hasOverflowX"):
		yield {"sev": "issue", "msg": f"horizontal overflow at 375px (scrollW={m.get('scrollW')} clientW={m.get('clientW')})"}
	yield {"sev": "info", "msg": f"viewport={m.get('viewportMeta')!r} 375px overflowX={m.get('hasOverflowX')}"}


def cat12_headings(p, all_pages):
	hs = p.get("headings", [])
	if not hs:
		yield {"sev": "warn", "msg": "no headings detected on page"}
		return
	h1s = [h for h in hs if h["level"] == 1]
	if len(h1s) == 0:
		yield {"sev": "issue", "msg": "no <h1> on page"}
	elif len(h1s) > 1:
		# multiple h1s often signal embedded sub-page (e.g. 404 component inside layout)
		texts = [h["text"][:30] for h in h1s]
		yield {"sev": "warn", "msg": f"{len(h1s)} <h1> elements: {texts}"}
	# heading level jumps (e.g., h1 then h4)
	jumps = []
	prev = None
	for h in hs:
		if prev is not None and h["level"] > prev + 1:
			jumps.append((prev, h["level"], h["text"][:40]))
		prev = h["level"]
	if jumps:
		yield {"sev": "warn", "msg": f"{len(jumps)} heading-level jumps: {jumps[:3]}"}
	# page title uniqueness (across the audit set)
	this_title = p.get("page_type")  # webmap inferred type — different from <title>


# titles handled separately so we can compare across pages
def cross_page_titles(all_pages):
	# this needs a different shape — yield page-scoped findings
	titles_by_url = {}
	for p in all_pages:
		# qa-pages records do not have <title> directly; we captured it in spa probe.
		# accept missing gracefully
		titles_by_url[p["target_url"]] = p.get("title")  # may be None
	return titles_by_url


CATEGORIES = [
	(1, "Landmarks & Structure", cat1_landmarks, False),
	(2, "Navigation Clarity", cat2_nav, False),
	(3, "Actions & Affordances", cat3_actions, False),
	(4, "Forms & Inputs", cat4_forms, False),
	(5, "A11y Name Coverage", cat5_ax, False),
	(6, "Label Hygiene", cat6_hygiene, False),
	(7, "Interactive Density", cat7_density, False),
	(8, "Auth Posture / System Status", cat8_auth, False),
	(9, "Routes & Network Health", cat9_routes, True),  # needs link_probe
	(10, "Runtime Errors & Broken Data", cat10_runtime, True),  # needs spa_probe
	(11, "Mobile Friendliness", cat11_mobile, False),
	(12, "Heading Order & Page Titles", cat12_headings, True),  # needs all_pages
]


def main():
	qa = json.loads(QA.read_text())
	link_probe = json.loads(LINK.read_text())
	spa_probe = json.loads(SPA.read_text())
	link_by_href = {r["href"]: r for r in link_probe}
	spa_by_url = {r["href"]: r for r in spa_probe}
	# also key spa by exact same target_url shape used in qa
	for r in spa_probe:
		spa_by_url[r["href"]] = r
	all_findings = []
	for page in qa:
		if "error" in page:
			continue
		for num, name, fn, needs_extra in CATEGORIES:
			try:
				if num == 9:
					gen = fn(page, link_by_href)
				elif num == 10:
					gen = fn(page, spa_by_url)
				elif num == 12:
					gen = fn(page, qa)
				else:
					gen = fn(page)
				for f in gen:
					all_findings.append({
						"url": page["target_url"],
						"cat": num,
						"category": name,
						"sev": f["sev"],
						"msg": f["msg"],
					})
			except Exception as e:
				all_findings.append({
					"url": page["target_url"], "cat": num, "category": name,
					"sev": "warn", "msg": f"category check errored: {e}",
				})
	OUTPUT.write_text(json.dumps(all_findings, indent=2))
	# rollup
	print("=" * 100)
	print(f"EXTENDED AUDIT — {len(qa)} pages × 12 categories — {len(all_findings)} findings")
	print()
	totals = Counter(f["sev"] for f in all_findings)
	print(f"TOTALS  issue={totals.get('issue', 0)}  warn={totals.get('warn', 0)}  info={totals.get('info', 0)}")
	print()
	print(f"{'cat':>4s} {'name':32s} {'issue':>6s} {'warn':>6s} {'info':>6s}")
	for num, name, *_ in CATEGORIES:
		cf = [f for f in all_findings if f["cat"] == num]
		c = Counter(f["sev"] for f in cf)
		print(f"{num:>4d} {name:32s} {c.get('issue', 0):>6d} {c.get('warn', 0):>6d} {c.get('info', 0):>6d}")
	print(f"\nfull JSON: {OUTPUT}")
	# top issues
	issues = [f for f in all_findings if f["sev"] == "issue"]
	if issues:
		print(f"\n--- {len(issues)} ISSUE findings ---")
		for f in issues:
			print(f"  [{f['cat']:2d}] {f['url'].split('/')[-1]:30s} {f['msg']}")


if __name__ == "__main__":
	main()
