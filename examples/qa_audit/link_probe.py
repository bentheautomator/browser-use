"""Probe all unique links from qa-pages.json via HEAD requests (GET fallback).

Output: tmp/link-probe.json — {href, status, redirect_chain, error}
"""
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

REPO = Path("/Users/automator/git/bentheautomator/browser-use")
INPUT = REPO / "tmp" / "qa-pages.json"
OUTPUT = REPO / "tmp" / "link-probe.json"
TIMEOUT = 10
CONCURRENCY = 10


async def probe(client: httpx.AsyncClient, href: str) -> dict:
	parsed = urlparse(href)
	if parsed.scheme not in ("http", "https"):
		return {"href": href, "skipped": "non-http scheme"}
	if not parsed.netloc:
		return {"href": href, "skipped": "empty netloc"}
	# strip anchor for probe
	probe_url = href.split("#")[0]
	chain = []
	try:
		r = await client.head(probe_url, timeout=TIMEOUT)
		# servers sometimes return 405 for HEAD; retry GET
		if r.status_code == 405:
			r = await client.get(probe_url, timeout=TIMEOUT)
		for h in r.history:
			chain.append({"status": h.status_code, "location": str(h.url)})
		return {
			"href": href,
			"probe_url": probe_url,
			"status": r.status_code,
			"final_url": str(r.url),
			"redirect_chain": chain,
		}
	except httpx.TimeoutException:
		return {"href": href, "error": "timeout"}
	except httpx.ConnectError as e:
		return {"href": href, "error": f"connect: {e}"}
	except Exception as e:
		return {"href": href, "error": str(e)[:200]}


async def main():
	data = json.loads(INPUT.read_text())
	# load storage to get session cookie for same-origin links
	storage = json.loads((REPO / "tmp" / "onplane.storage.json").read_text())
	cookies = {c["name"]: c["value"] for c in storage.get("cookies", [])}
	# collect unique hrefs across all pages
	hrefs: dict[str, list[str]] = {}
	for page in data:
		if "error" in page:
			continue
		page_url = page["target_url"]
		for ln in page.get("all_links", []):
			h = (ln.get("href") or "").strip()
			if not h or h.startswith("javascript:") or h.startswith("mailto:"):
				continue
			hrefs.setdefault(h, []).append(page_url)
	print(f"[probe] {len(hrefs)} unique hrefs across {len(data)} pages")
	sem = asyncio.Semaphore(CONCURRENCY)
	async with httpx.AsyncClient(
		follow_redirects=True,
		cookies=cookies,
		headers={"User-Agent": "qa-audit/1.0 (+browser-use)"},
	) as client:
		async def bounded_probe(h):
			async with sem:
				r = await probe(client, h)
				print(
					f"[probe] {r.get('status', r.get('error', r.get('skipped', '?')))} "
					f"{h[:90]}",
					flush=True,
				)
				return r

		results = await asyncio.gather(*[bounded_probe(h) for h in hrefs.keys()])
	# attach source pages
	for r in results:
		r["found_on_pages"] = hrefs.get(r["href"], [])
	OUTPUT.write_text(json.dumps(results, indent=2))
	# rollup
	from collections import Counter
	statuses = Counter()
	for r in results:
		if "status" in r:
			statuses[f"{r['status'] // 100}xx"] += 1
		elif "error" in r:
			statuses["error"] += 1
		else:
			statuses["skipped"] += 1
	print(f"\n[probe] rollup: {dict(statuses)} -> {OUTPUT}")


if __name__ == "__main__":
	asyncio.run(main())
