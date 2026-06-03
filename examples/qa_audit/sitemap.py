"""Sitemap discovery: URLs + route templates + API inventory.

BFS-crawls an authenticated SPA from a seed list. For each page reached:
  - records all <a href> as outbound URL edges
  - records every XHR/fetch network request as an API call
  - records the response status for each API call

Outputs (tmp/):
  sitemap-urls.json       — adjacency: {url, depth, parent, outbound_links[]}
  sitemap-routes.json     — clustered URL templates: /t/[slug]/[page]
  sitemap-apis.json       — unique API endpoints: {method, path_template, statuses[],
                                                   called_from_pages[]}

Run after capture_login.py.
"""
import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

REPO = Path("/Users/automator/git/bentheautomator/browser-use")
sys.path.insert(0, str(REPO))

from browser_use.integrations.webmap import AuthScanSession

STORAGE = REPO / "tmp" / "onplane.storage.json"
BASE = "http://localhost:5234"
SEEDS = [
	"/t/on-plane-consulting/client-metrics",
	"/t/on-plane-consulting/sales-pipeline",
	"/t/on-plane-consulting/capacity",
]
MAX_DEPTH = 2
MAX_PAGES = 30
HYDRATION_SEC = 4
OUT_URLS = REPO / "tmp" / "sitemap-urls.json"
OUT_ROUTES = REPO / "tmp" / "sitemap-routes.json"
OUT_APIS = REPO / "tmp" / "sitemap-apis.json"

# templates to normalize: replace UUIDs, hex IDs, numeric IDs, slugs
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
HEX_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.I)
NUM_RE = re.compile(r"\b\d{3,}\b")
SLUG_LIKE = re.compile(r"/[a-z0-9][a-z0-9\-_]{2,}(?=/|$)")


def normalize_path(path: str) -> str:
	p = UUID_RE.sub("[uuid]", path)
	p = HEX_RE.sub("[hex]", p)
	p = NUM_RE.sub("[id]", p)
	return p


def route_template_from_url(url: str) -> str:
	"""Cluster on-plane workspace routes: /t/[slug]/[page] etc.

	Replaces the workspace slug after /t/ with [slug] so all tenants collapse to
	the same template, but keeps the page leaf as-is.
	"""
	parsed = urlparse(url)
	parts = parsed.path.split("/")
	# /t/<slug>/anything → /t/[slug]/anything
	if len(parts) >= 3 and parts[1] == "t":
		parts[2] = "[slug]"
	# then normalize id-like segments
	return normalize_path("/".join(parts))


async def crawl_one(session, url: str) -> dict:
	"""Visit a URL, capture outbound links + API calls."""
	cdp = await session.get_or_create_cdp_session(target_id=None, focus=False)
	sid = cdp.session_id

	# capture buffers
	api_calls: list[dict] = []
	in_flight: dict[str, dict] = {}

	def on_request_will_be_sent(p, session_id=None):
		try:
			rid = p.get("requestId")
			req = p.get("request") or {}
			rurl = req.get("url") or ""
			rtype = p.get("type", "")
			# we only care about XHR / fetch (= API calls), not Document / Script / Image
			if rtype in ("XHR", "Fetch"):
				in_flight[rid] = {
					"url": rurl,
					"method": req.get("method", "GET"),
				}
		except Exception:
			pass

	def on_response(p, session_id=None):
		try:
			rid = p.get("requestId")
			if rid in in_flight:
				r = p.get("response") or {}
				rec = in_flight.pop(rid)
				rec["status"] = r.get("status")
				rec["mime"] = r.get("mimeType", "")
				api_calls.append(rec)
		except Exception:
			pass

	await cdp.cdp_client.send.Network.enable(session_id=sid)
	await cdp.cdp_client.send.Runtime.enable(session_id=sid)
	cdp.cdp_client.register.Network.requestWillBeSent(on_request_will_be_sent)
	cdp.cdp_client.register.Network.responseReceived(on_response)

	await session._cdp_navigate(url)
	await asyncio.sleep(HYDRATION_SEC)
	landed = await session.get_current_page_url()

	# DOM link enumeration
	links_eval = await cdp.cdp_client.send.Runtime.evaluate(
		params={
			"expression": (
				"Array.from(document.querySelectorAll('a[href]'))"
				".map(a => a.href).filter(h => h && !h.startsWith('javascript:') && !h.startsWith('mailto:'))"
			),
			"returnByValue": True,
		},
		session_id=sid,
	)
	out_links = (links_eval.get("result", {}) or {}).get("value", []) or []

	await cdp.cdp_client.send.Network.disable(session_id=sid)
	await cdp.cdp_client.send.Runtime.disable(session_id=sid)

	return {
		"url": url,
		"landed_url": landed,
		"outbound_links": sorted(set(out_links)),
		"api_calls": api_calls,
	}


def is_same_origin(url: str, base: str = BASE) -> bool:
	return urlparse(url).netloc == urlparse(base).netloc


async def main():
	seeds_full = [BASE + s if not s.startswith("http") else s for s in SEEDS]
	visited: set[str] = set()
	# (url, depth, parent)
	queue: list[tuple[str, int, str | None]] = [(s, 0, None) for s in seeds_full]
	# normalize URLs by stripping fragment
	def canon(u: str) -> str:
		return u.split("#")[0]
	pages: list[dict] = []
	parent_map: dict[str, str | None] = {}
	depth_map: dict[str, int] = {}

	async with AuthScanSession(STORAGE, headless=True) as session:
		while queue and len(visited) < MAX_PAGES:
			url, depth, parent = queue.pop(0)
			cu = canon(url)
			if cu in visited:
				continue
			if not is_same_origin(cu):
				continue
			if depth > MAX_DEPTH:
				continue
			visited.add(cu)
			parent_map[cu] = parent
			depth_map[cu] = depth
			print(f"[crawl] depth={depth} {cu}", flush=True)
			try:
				rec = await crawl_one(session, cu)
			except Exception as e:
				print(f"[crawl] ERROR {cu}: {e}", flush=True)
				continue
			rec["depth"] = depth
			rec["parent"] = parent
			pages.append(rec)
			# enqueue outbound same-origin links
			for href in rec["outbound_links"]:
				ch = canon(href)
				if ch not in visited and is_same_origin(ch):
					queue.append((ch, depth + 1, cu))

	# write URLs file
	OUT_URLS.write_text(json.dumps(pages, indent=2))
	print(f"\n[crawl] visited {len(visited)} URLs -> {OUT_URLS}", flush=True)

	# build route templates
	templates: dict[str, list[str]] = defaultdict(list)
	for url in sorted(visited):
		templates[route_template_from_url(url)].append(url)
	route_records = [
		{
			"template": tpl,
			"instances": urls,
			"instance_count": len(urls),
		}
		for tpl, urls in sorted(templates.items())
	]
	OUT_ROUTES.write_text(json.dumps(route_records, indent=2))
	print(f"[crawl] {len(route_records)} route templates -> {OUT_ROUTES}", flush=True)

	# build API inventory
	api_groups: dict[tuple[str, str], dict] = {}
	for page in pages:
		for call in page.get("api_calls", []):
			method = call["method"]
			tpl = route_template_from_url(call["url"])
			key = (method, tpl)
			rec = api_groups.setdefault(key, {
				"method": method,
				"path_template": tpl,
				"statuses": defaultdict(int),
				"called_from_pages": set(),
				"sample_urls": set(),
			})
			rec["statuses"][call.get("status", 0)] += 1
			rec["called_from_pages"].add(page["url"])
			if len(rec["sample_urls"]) < 5:
				rec["sample_urls"].add(call["url"])
	api_records = []
	for rec in api_groups.values():
		api_records.append({
			"method": rec["method"],
			"path_template": rec["path_template"],
			"statuses": dict(rec["statuses"]),
			"called_from_pages": sorted(rec["called_from_pages"]),
			"sample_urls": sorted(rec["sample_urls"]),
			"total_calls": sum(rec["statuses"].values()),
		})
	api_records.sort(key=lambda r: (-r["total_calls"], r["path_template"]))
	OUT_APIS.write_text(json.dumps(api_records, indent=2))
	print(f"[crawl] {len(api_records)} unique API endpoints -> {OUT_APIS}", flush=True)

	# rollup print
	print("\n=== URLS ===")
	for url in sorted(visited):
		print(f"  d={depth_map[url]} {url}")
	print(f"\n=== ROUTES ({len(route_records)} templates) ===")
	for r in route_records:
		print(f"  ×{r['instance_count']:2d} {r['template']}")
	print(f"\n=== APIS ({len(api_records)} endpoints) ===")
	for r in api_records[:30]:
		statuses = ",".join(f"{k}×{v}" for k, v in sorted(r["statuses"].items()))
		print(f"  {r['method']:6s} {r['path_template']:60s} {statuses}")
	if len(api_records) > 30:
		print(f"  ... +{len(api_records) - 30} more")


if __name__ == "__main__":
	asyncio.run(main())
