"""Scan client-metrics under saved auth state."""
import asyncio
import sys
from pathlib import Path

REPO = Path("/Users/automator/git/bentheautomator/browser-use")
sys.path.insert(0, str(REPO))

from browser_use.integrations.webmap import AuthScanSession, WebmapExtractor

STORAGE = REPO / "tmp" / "onplane.storage.json"
URL = "http://localhost:5234/t/on-plane-consulting/client-metrics"


async def main():
	async with AuthScanSession(STORAGE, headless=True) as session:
		print(f"[scan] navigating to {URL}", flush=True)
		await session._cdp_navigate(URL)
		await asyncio.sleep(4)
		landed = await session.get_current_page_url()
		print(f"[scan] landed_url: {landed}", flush=True)
		extractor = WebmapExtractor()
		result = await asyncio.wait_for(extractor.scan_current_page(session), timeout=45)
		print("=" * 60)
		print(f"page_type:           {result.page_type}")
		print(f"page_purpose:        {result.page_purpose}")
		print(f"interactive_count:   {result.interactive_count}")
		print(f"enriched_via_ax:     {result.enriched_via_ax_tree}")
		print(f"webmap buttons:      {result.buttons[:10]}")
		print(f"webmap links (10):   {result.links[:10]}")
		print(f"webmap forms:        {result.forms}")
		print(f"keywords:            {result.keywords[:10]}")
		print(f"ax_interactives ({len(result.ax_interactives)}):")
		for n in result.ax_interactives[:30]:
			print(f"  role={n['role']!r:12} name={n['name']!r}")
		print("=" * 60)
		print("RAW webmap (2500 chars):")
		print(result.raw[:2500])


if __name__ == "__main__":
	asyncio.run(main())
