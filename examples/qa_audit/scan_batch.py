"""Batch scan the 3 target pages under saved auth state."""
import asyncio
import json
import sys
from pathlib import Path

REPO = Path("/Users/automator/git/bentheautomator/browser-use")
sys.path.insert(0, str(REPO))

from browser_use.integrations.webmap import AuthScanSession, WebmapExtractor

STORAGE = REPO / "tmp" / "onplane.storage.json"
BASE = "http://localhost:5234"
PAGES = [
	"/t/on-plane-consulting/client-metrics",
	"/t/on-plane-consulting/sales-pipeline",
	"/t/on-plane-consulting/capacity",
]
OUT = REPO / "tmp" / "scan-results.json"


async def main():
	results = []
	async with AuthScanSession(STORAGE, headless=True) as session:
		extractor = WebmapExtractor()
		for path in PAGES:
			url = BASE + path
			print(f"\n[batch] scanning {url}", flush=True)
			try:
				await session._cdp_navigate(url)
				await asyncio.sleep(4)  # SPA hydration buffer
				landed = await session.get_current_page_url()
				result = await asyncio.wait_for(
					extractor.scan_current_page(session), timeout=45
				)
				summary = {
					"target_url": url,
					"landed_url": landed,
					"page_type": result.page_type,
					"page_purpose": result.page_purpose,
					"interactive_count": result.interactive_count,
					"enriched_via_ax_tree": result.enriched_via_ax_tree,
					"buttons": result.buttons,
					"links": result.links,
					"forms": result.forms,
					"inputs": result.inputs,
					"regions": result.regions,
					"keywords": result.keywords,
					"ax_interactives": result.ax_interactives,
					"raw_webmap": result.raw,
				}
				results.append(summary)
				print(
					f"[batch] {path} -> type={result.page_type!r} "
					f"interactives={result.interactive_count} "
					f"ax_fallback={result.enriched_via_ax_tree}",
					flush=True,
				)
			except Exception as e:
				print(f"[batch] ERROR on {path}: {e}", flush=True)
				results.append({"target_url": url, "error": str(e)})
	OUT.write_text(json.dumps(results, indent=2))
	print(f"\n[batch] wrote {OUT} ({OUT.stat().st_size} bytes, {len(results)} pages)", flush=True)
	# headline summary
	print("\n=== SUMMARY ===")
	for r in results:
		if "error" in r:
			print(f"  {r['target_url']:60s} ERROR: {r['error']}")
		else:
			print(
				f"  {r['target_url']:60s} type={r['page_type']:10s} "
				f"i={r['interactive_count']:3d} "
				f"buttons={len(r['buttons']):2d} "
				f"links={len(r['links']):2d} "
				f"ax={r['enriched_via_ax_tree']}"
			)


if __name__ == "__main__":
	asyncio.run(main())
