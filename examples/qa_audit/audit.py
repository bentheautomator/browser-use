"""8-category audit driver consuming tmp/scan-results.json.

Categories (synthesized from proposed UX heuristics + Nielsen 10 + WCAG 2.2 AA):
  1. Landmarks & Structure        (WCAG 1.3.1, Nielsen consistency)
  2. Navigation Clarity           (WCAG 2.4, Nielsen recognition>recall)
  3. Actions & Affordances        (Nielsen match real-world)
  4. Forms & Inputs               (WCAG 3.3.2, Nielsen error prevention)
  5. A11y Name Coverage           (WCAG 4.1.2 Name/Role/Value)
  6. Label Hygiene                (WCAG 2.4.6, Nielsen consistency)
  7. Interactive Density          (Nielsen aesthetic/minimalist)
  8. Auth Posture / System Status (WCAG 4.1.3, Nielsen visibility)

Severity:
  info  — observation, no action
  warn  — likely issue, investigate
  issue — definite problem, fix
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path("/Users/automator/git/bentheautomator/browser-use")
INPUT = REPO / "tmp" / "scan-results.json"
OUTPUT = REPO / "tmp" / "audit-findings.json"

AMBIGUOUS_LABELS = {
	"button", "click here", "click", "more", "submit", "ok", "yes", "no",
	"link", "here", "read more", "...", "?", "!", "...",
}
EXPECTED_LANDMARKS = {"nav", "aside", "main", "header", "footer"}
INTERACTIVE_ROLES_EXPECTED = {"button", "link", "textbox"}


def category_1_landmarks(page: dict) -> list[dict]:
	out = []
	regions = [r.lower() for r in page.get("regions", [])]
	region_text = " ".join(regions)
	found = {lm for lm in EXPECTED_LANDMARKS if lm in region_text}
	missing = EXPECTED_LANDMARKS - found
	if len(found) < 3:
		out.append({
			"cat": 1, "sev": "issue",
			"msg": f"only {len(found)} landmarks detected (found={sorted(found)}, missing={sorted(missing)})",
			"value": list(found),
		})
	elif missing:
		out.append({
			"cat": 1, "sev": "warn",
			"msg": f"landmarks present but missing {sorted(missing)}",
			"value": list(found),
		})
	else:
		out.append({"cat": 1, "sev": "info", "msg": f"all expected landmarks present", "value": sorted(found)})
	return out


def category_2_nav(page: dict) -> list[dict]:
	out = []
	links = page.get("links", [])
	n = len(links)
	if n == 0:
		out.append({"cat": 2, "sev": "issue", "msg": "zero links — no navigation surface", "value": 0})
		return out
	if n > 50:
		out.append({"cat": 2, "sev": "warn", "msg": f"{n} links — likely link dump, group via nav regions", "value": n})
	# label quality: links should have non-trivial text
	short = [l for l in links if len(l.strip()) < 3]
	if short:
		out.append({
			"cat": 2, "sev": "warn",
			"msg": f"{len(short)} links have <3 char labels", "value": short[:10],
		})
	out.append({"cat": 2, "sev": "info", "msg": f"{n} links total", "value": n})
	return out


def category_3_actions(page: dict) -> list[dict]:
	out = []
	buttons = page.get("buttons", [])
	n = len(buttons)
	if n == 0:
		out.append({"cat": 3, "sev": "warn", "msg": "zero buttons — read-only page or affordance gap", "value": 0})
		return out
	ambiguous = [b for b in buttons if b.strip().lower() in AMBIGUOUS_LABELS]
	if ambiguous:
		out.append({
			"cat": 3, "sev": "issue",
			"msg": f"{len(ambiguous)} ambiguous button labels",
			"value": ambiguous,
		})
	# buttons should not include URLs or look like menu joins ("Section(N)" style)
	menus = [b for b in buttons if re.search(r"\(\d+\)$", b)]
	if menus:
		out.append({
			"cat": 3, "sev": "info",
			"msg": f"{len(menus)} buttons look like collapsible groups (Section(N))",
			"value": menus,
		})
	out.append({"cat": 3, "sev": "info", "msg": f"{n} buttons total", "value": n})
	return out


def category_4_forms(page: dict) -> list[dict]:
	out = []
	forms = page.get("forms", 0)
	inputs = page.get("inputs", 0)
	if forms == 0 and inputs > 0:
		out.append({
			"cat": 4, "sev": "warn",
			"msg": f"{inputs} inputs but 0 forms — inputs not wrapped in <form>",
			"value": {"forms": forms, "inputs": inputs},
		})
	out.append({"cat": 4, "sev": "info", "msg": f"forms={forms} inputs={inputs}", "value": {"forms": forms, "inputs": inputs}})
	return out


def category_5_ax_name_coverage(page: dict) -> list[dict]:
	out = []
	ax = page.get("ax_interactives", [])
	enriched = page.get("enriched_via_ax_tree", False)
	if not enriched and not ax:
		out.append({"cat": 5, "sev": "info", "msg": "webmap was sufficient, AX tree not sampled", "value": None})
		return out
	if not ax:
		out.append({"cat": 5, "sev": "warn", "msg": "AX tree fallback fired but returned 0 nodes", "value": 0})
		return out
	unnamed = [n for n in ax if not n.get("name")]
	pct = round(100 * len(unnamed) / len(ax), 1) if ax else 0
	sev = "issue" if pct > 20 else "warn" if pct > 5 else "info"
	out.append({
		"cat": 5, "sev": sev,
		"msg": f"{len(unnamed)}/{len(ax)} AX nodes ({pct}%) have empty accessible name",
		"value": [n["role"] for n in unnamed[:10]],
	})
	roles = Counter(n["role"] for n in ax)
	out.append({"cat": 5, "sev": "info", "msg": f"AX role mix: {dict(roles)}", "value": dict(roles)})
	return out


def category_6_label_hygiene(page: dict) -> list[dict]:
	out = []
	buttons = page.get("buttons", [])
	links = page.get("links", [])
	all_labels = [l.strip() for l in buttons + links if l.strip()]
	dupes = [k for k, v in Counter(all_labels).items() if v > 1]
	if dupes:
		out.append({
			"cat": 6, "sev": "warn",
			"msg": f"{len(dupes)} duplicate button/link labels",
			"value": dupes[:10],
		})
	# look for labels containing "http" or "/" (likely URLs leaked into text)
	url_leaks = [l for l in all_labels if "http" in l.lower() or l.startswith("/")]
	if url_leaks:
		out.append({
			"cat": 6, "sev": "issue",
			"msg": f"{len(url_leaks)} labels look like URLs (raw href leaked)",
			"value": url_leaks[:10],
		})
	if not out:
		out.append({"cat": 6, "sev": "info", "msg": "no obvious label hygiene issues", "value": None})
	return out


def category_7_density(page: dict) -> list[dict]:
	out = []
	interactives = page.get("interactive_count", 0)
	regions = len(page.get("regions", []))
	if regions == 0:
		ratio = float("inf")
	else:
		ratio = round(interactives / regions, 1)
	# >12 interactives per region usually means the page packs too much into one zone
	sev = "warn" if ratio > 15 else "info"
	out.append({
		"cat": 7, "sev": sev,
		"msg": f"{interactives} interactives across {regions} regions (ratio {ratio}/region)",
		"value": {"interactives": interactives, "regions": regions, "ratio": ratio},
	})
	return out


def category_8_auth_posture(page: dict) -> list[dict]:
	out = []
	labels = [l.lower() for l in page.get("buttons", []) + page.get("links", [])]
	label_blob = " | ".join(labels)
	sign_out_present = "sign out" in label_blob or "log out" in label_blob or "logout" in label_blob
	login_form_present = (
		"send sign-in link" in label_blob
		or "send magic link" in label_blob
		or "sign in" in label_blob and not sign_out_present
	)
	landed = page.get("landed_url", "")
	on_login_url = "/login" in landed
	if on_login_url:
		out.append({
			"cat": 8, "sev": "issue",
			"msg": f"landed on /login despite expecting authenticated content: {landed}",
			"value": landed,
		})
	elif login_form_present and not sign_out_present:
		out.append({
			"cat": 8, "sev": "issue",
			"msg": "page renders login form but no Sign Out — auth gate failed",
			"value": None,
		})
	elif sign_out_present:
		out.append({"cat": 8, "sev": "info", "msg": "Sign Out present — authenticated", "value": None})
	else:
		out.append({
			"cat": 8, "sev": "warn",
			"msg": "no Sign Out and no login form — ambiguous auth signal",
			"value": None,
		})
	# page_type drift
	expected_types = {"page", "dashboard", "home", "app"}
	pt = page.get("page_type", "")
	if pt and pt not in expected_types:
		out.append({
			"cat": 8, "sev": "warn",
			"msg": f"webmap inferred page_type={pt!r} — verify against intent",
			"value": pt,
		})
	return out


CATEGORIES = [
	("Landmarks & Structure", category_1_landmarks),
	("Navigation Clarity", category_2_nav),
	("Actions & Affordances", category_3_actions),
	("Forms & Inputs", category_4_forms),
	("A11y Name Coverage", category_5_ax_name_coverage),
	("Label Hygiene", category_6_label_hygiene),
	("Interactive Density", category_7_density),
	("Auth Posture", category_8_auth_posture),
]


def audit_page(page: dict) -> list[dict]:
	findings = []
	for name, fn in CATEGORIES:
		for f in fn(page):
			f["category_name"] = name
			f["url"] = page.get("target_url")
			findings.append(f)
	return findings


def main():
	data = json.loads(INPUT.read_text())
	all_findings = []
	for page in data:
		if "error" in page:
			print(f"SKIP error page: {page['target_url']}: {page['error']}")
			continue
		all_findings.extend(audit_page(page))
	OUTPUT.write_text(json.dumps(all_findings, indent=2))
	# table report grouped by page x category
	pages = sorted({f["url"] for f in all_findings})
	print("=" * 120)
	print(f"AUDIT REPORT — 8 categories × {len(pages)} pages — {len(all_findings)} findings\n")
	for url in pages:
		print(f"\n● {url}")
		page_findings = [f for f in all_findings if f["url"] == url]
		for cat_num in range(1, 9):
			cat_name = CATEGORIES[cat_num - 1][0]
			cf = [f for f in page_findings if f["cat"] == cat_num]
			worst = "issue" if any(f["sev"] == "issue" for f in cf) else "warn" if any(f["sev"] == "warn" for f in cf) else "info"
			icon = {"issue": "❌", "warn": "⚠️ ", "info": "✓ "}[worst]
			print(f"  {icon} [{cat_num}] {cat_name}")
			for f in cf:
				print(f"      {f['sev']:5s}: {f['msg']}")
	# rollup
	print("\n" + "=" * 120)
	print("ROLLUP — count by severity per category")
	print(f"  {'cat':3s} {'name':28s} {'issue':>6s} {'warn':>6s} {'info':>6s}")
	for i, (name, _) in enumerate(CATEGORIES, 1):
		cf = [f for f in all_findings if f["cat"] == i]
		c = Counter(f["sev"] for f in cf)
		print(f"  {i:3d} {name:28s} {c.get('issue', 0):>6d} {c.get('warn', 0):>6d} {c.get('info', 0):>6d}")
	totals = Counter(f["sev"] for f in all_findings)
	print(f"  {'TOTAL':3s} {'':28s} {totals.get('issue', 0):>6d} {totals.get('warn', 0):>6d} {totals.get('info', 0):>6d}")
	print(f"\nFull findings JSON: {OUTPUT}")


if __name__ == "__main__":
	main()
