"""SPA-level route probe: navigate each unique link via authenticated browser
and capture heading text + 404/error markers. Catches client-side 404s that
HEAD probes miss.
"""
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO = Path("/Users/automator/git/bentheautomator/browser-use")
sys.path.insert(0, str(REPO))

from browser_use.integrations.webmap import AuthScanSession

STORAGE = REPO / "tmp" / "onplane.storage.json"
INPUT = REPO / "tmp" / "qa-pages.json"
OUTPUT = REPO / "tmp" / "spa-route-probe.json"

NOT_FOUND_MARKERS = [
	"404",
	"Page Not Found",
	"page not found",
	"Not Found",
	"This page could not be found",
]
ERROR_BOUNDARY_MARKERS = [
	"Something went wrong",
	"Application error",
	"An error occurred",
	"Failed to load",
	"Unable to load",
]


async def probe_one(session, href: str) -> dict:
	cdp = await session.get_or_create_cdp_session(target_id=None, focus=False)
	sid = cdp.session_id
	probe_url = href.split("#")[0]
	out = {"href": href, "probe_url": probe_url}
	try:
		await session._cdp_navigate(probe_url)
		await asyncio.sleep(3)
		out["landed_url"] = await session.get_current_page_url()
		# headings
		h_eval = await cdp.cdp_client.send.Runtime.evaluate(
			params={
				"expression": (
					"Array.from(document.querySelectorAll('h1,h2,h3'))"
					".map(h => ({level: parseInt(h.tagName[1]), text: (h.textContent || '').trim().slice(0, 80)}))"
				),
				"returnByValue": True,
			},
			session_id=sid,
		)
		headings = (h_eval.get("result", {}) or {}).get("value", []) or []
		out["headings"] = headings
		# title
		t_eval = await cdp.cdp_client.send.Runtime.evaluate(
			params={"expression": "document.title || ''", "returnByValue": True},
			session_id=sid,
		)
		out["title"] = (t_eval.get("result", {}) or {}).get("value", "")
		# scan body text for markers
		b_eval = await cdp.cdp_client.send.Runtime.evaluate(
			params={"expression": "document.body && document.body.innerText.slice(0, 5000) || ''", "returnByValue": True},
			session_id=sid,
		)
		body = (b_eval.get("result", {}) or {}).get("value", "") or ""
		out["not_found_markers"] = [m for m in NOT_FOUND_MARKERS if m in body]
		out["error_boundary_markers"] = [m for m in ERROR_BOUNDARY_MARKERS if m in body]
		# in-DOM 404 = heading text mentions 404/Not Found AND not the desired page
		heading_text = " ".join(h["text"] for h in headings).lower()
		out["spa_404"] = ("404" in heading_text or "page not found" in heading_text)
	except Exception as e:
		out["error"] = str(e)[:200]
	return out


async def main():
	data = json.loads(INPUT.read_text())
	hrefs: set[str] = set()
	for page in data:
		if "error" in page:
			continue
		for ln in page.get("all_links", []):
			h = (ln.get("href") or "").strip()
			if not h or h.startswith("javascript:") or h.startswith("mailto:"):
				continue
			# only same-origin links worth navigating
			p = urlparse(h)
			if p.scheme not in ("http", "https"):
				continue
			if p.netloc and p.netloc != urlparse(page["target_url"]).netloc:
				continue  # skip external links; HEAD probe is enough
			# strip anchor for dedupe
			hrefs.add(h.split("#")[0])
	hrefs_list = sorted(hrefs)
	print(f"[spa] probing {len(hrefs_list)} unique same-origin routes", flush=True)
	results = []
	async with AuthScanSession(STORAGE, headless=True) as session:
		for h in hrefs_list:
			r = await probe_one(session, h)
			results.append(r)
			marker = "❌SPA-404" if r.get("spa_404") else ("⚠️ERROR" if r.get("error_boundary_markers") else "✓")
			print(
				f"[spa] {marker:10s} {r.get('landed_url') or h:80s} "
				f"title={r.get('title', '')[:40]!r}",
				flush=True,
			)
	OUTPUT.write_text(json.dumps(results, indent=2))
	# rollup
	spa_404 = [r for r in results if r.get("spa_404")]
	err_bnd = [r for r in results if r.get("error_boundary_markers")]
	probe_err = [r for r in results if r.get("error")]
	print(f"\n[spa] {len(results)} routes probed:")
	print(f"  SPA-404:       {len(spa_404)} {[r['href'] for r in spa_404]}")
	print(f"  error-bound:   {len(err_bnd)} {[r['href'] for r in err_bnd]}")
	print(f"  probe-error:   {len(probe_err)} {[r['href'] for r in probe_err]}")


if __name__ == "__main__":
	asyncio.run(main())
