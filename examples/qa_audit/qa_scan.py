"""Extended QA scanner: webmap + console + network + mobile + link enum.

For each target URL:
  1. Reset capture buffers; subscribe to CDP Network + Runtime events
  2. Navigate; wait for SPA hydration buffer
  3. Capture:
       - main document status code
       - all 4xx/5xx network responses
       - failed requests
       - console messages with level=error|warning
       - JS uncaught exceptions
       - webmap scan (existing pipeline, includes AX fallback)
       - all <a href> on the page (for link probing later)
       - text snippet for broken-data detection
  4. Resize viewport to 375x812 (mobile), capture overflow + visible interactives
  5. Reset to desktop, move to next URL

Output: tmp/qa-pages.json (one record per URL)
"""
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

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
OUT = REPO / "tmp" / "qa-pages.json"
HYDRATION_SEC = 5
MOBILE_W = 375
MOBILE_H = 812
DESKTOP_W = 1440
DESKTOP_H = 900

BROKEN_DATA_MARKERS = [
	"undefined",
	"[object Object]",
	"NaN",
	"Error:",
	"TypeError",
	"ReferenceError",
	"500 Internal",
	"404 Not Found",
	"Failed to fetch",
	"Application error",
	"Something went wrong",
]


async def collect_for_url(session, url: str) -> dict:
	cdp = await session.get_or_create_cdp_session(target_id=None, focus=False)
	sid = cdp.session_id

	# capture buffers
	main_status = {"code": None, "url": None}
	bad_responses: list[dict] = []
	failed_requests: list[dict] = []
	console_msgs: list[dict] = []
	exceptions: list[dict] = []

	def on_response(p, session_id=None):
		try:
			r = p.get("response", {})
			status = r.get("status", 0)
			rurl = r.get("url", "")
			if main_status["code"] is None and rurl.startswith(BASE):
				if p.get("type") == "Document":
					main_status["code"] = status
					main_status["url"] = rurl
			if 400 <= status < 600:
				bad_responses.append({"status": status, "url": rurl, "type": p.get("type")})
		except Exception:
			pass

	def on_loading_failed(p, session_id=None):
		failed_requests.append({
			"url": p.get("url") or p.get("documentURL") or "",
			"reason": p.get("errorText", ""),
			"type": p.get("type"),
		})

	def on_console(p, session_id=None):
		try:
			level = p.get("type", "")
			if level not in ("error", "warning"):
				return
			args = p.get("args", []) or []
			texts = []
			for a in args:
				v = a.get("value")
				if v is None:
					v = a.get("description") or a.get("preview", {}).get("description", "")
				texts.append(str(v))
			console_msgs.append({"level": level, "text": " ".join(texts)[:500]})
		except Exception:
			pass

	def on_exception(p, session_id=None):
		try:
			d = p.get("exceptionDetails", {})
			exceptions.append({
				"text": d.get("text", ""),
				"url": d.get("url", ""),
				"line": d.get("lineNumber"),
				"exception": (d.get("exception") or {}).get("description", "")[:500],
			})
		except Exception:
			pass

	# enable + register
	await cdp.cdp_client.send.Network.enable(session_id=sid)
	await cdp.cdp_client.send.Runtime.enable(session_id=sid)
	cdp.cdp_client.register.Network.responseReceived(on_response)
	cdp.cdp_client.register.Network.loadingFailed(on_loading_failed)
	cdp.cdp_client.register.Runtime.consoleAPICalled(on_console)
	cdp.cdp_client.register.Runtime.exceptionThrown(on_exception)

	# navigate
	print(f"\n[qa] navigating {url}", flush=True)
	await session._cdp_navigate(url)
	await asyncio.sleep(HYDRATION_SEC)
	landed = await session.get_current_page_url()
	print(f"[qa] landed_url: {landed}  main_status: {main_status['code']}", flush=True)

	# webmap + AX
	extractor = WebmapExtractor()
	wm = await asyncio.wait_for(extractor.scan_current_page(session), timeout=45)

	# raw HTML for broken-data + link enumeration
	html = await extractor._get_page_content(session) or ""

	# enumerate links via JS
	link_eval = await cdp.cdp_client.send.Runtime.evaluate(
		params={
			"expression": (
				"Array.from(document.querySelectorAll('a[href]')).map(a => ({"
				"href: a.href, text: (a.innerText || a.textContent || '').trim().slice(0, 80)"
				"}))"
			),
			"returnByValue": True,
		},
		session_id=sid,
	)
	links: list[dict] = []
	try:
		links = link_eval.get("result", {}).get("value", []) or []
	except Exception:
		pass

	# broken-data marker scan in text content
	text_eval = await cdp.cdp_client.send.Runtime.evaluate(
		params={"expression": "document.body && document.body.innerText || ''", "returnByValue": True},
		session_id=sid,
	)
	body_text = (text_eval.get("result", {}) or {}).get("value", "") or ""
	broken_markers = [
		m for m in BROKEN_DATA_MARKERS if m.lower() in body_text.lower()
	]

	# mobile viewport pass
	await cdp.cdp_client.send.Emulation.setDeviceMetricsOverride(
		params={
			"width": MOBILE_W,
			"height": MOBILE_H,
			"deviceScaleFactor": 2,
			"mobile": True,
		},
		session_id=sid,
	)
	await asyncio.sleep(2)
	mobile_eval = await cdp.cdp_client.send.Runtime.evaluate(
		params={
			"expression": (
				"({"
				"scrollW: document.documentElement.scrollWidth,"
				"clientW: document.documentElement.clientWidth,"
				"hasOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,"
				"viewportMeta: (document.querySelector('meta[name=\"viewport\"]') || {}).content || null,"
				"visibleNavs: document.querySelectorAll('nav:not([hidden])').length,"
				"hiddenAsides: document.querySelectorAll('aside[hidden], aside[aria-hidden=\"true\"]').length"
				"})"
			),
			"returnByValue": True,
		},
		session_id=sid,
	)
	mobile_data = (mobile_eval.get("result", {}) or {}).get("value", {}) or {}

	# reset viewport
	await cdp.cdp_client.send.Emulation.clearDeviceMetricsOverride(session_id=sid)

	# stop event captures so they don't leak into next URL
	await cdp.cdp_client.send.Network.disable(session_id=sid)
	await cdp.cdp_client.send.Runtime.disable(session_id=sid)

	# deep a11y: heading order
	# (re-run runtime briefly)
	await cdp.cdp_client.send.Runtime.enable(session_id=sid)
	heading_eval = await cdp.cdp_client.send.Runtime.evaluate(
		params={
			"expression": (
				"Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))"
				".map(h => ({level: parseInt(h.tagName[1]), text: (h.textContent || '').trim().slice(0, 100)}))"
			),
			"returnByValue": True,
		},
		session_id=sid,
	)
	headings = (heading_eval.get("result", {}) or {}).get("value", []) or []
	await cdp.cdp_client.send.Runtime.disable(session_id=sid)

	return {
		"target_url": url,
		"landed_url": landed,
		"main_status": main_status["code"],
		"main_status_url": main_status["url"],
		"bad_responses": bad_responses,
		"failed_requests": failed_requests,
		"console_msgs": console_msgs,
		"exceptions": exceptions,
		"broken_data_markers_found": broken_markers,
		"page_type": wm.page_type,
		"page_purpose": wm.page_purpose,
		"interactive_count": wm.interactive_count,
		"enriched_via_ax_tree": wm.enriched_via_ax_tree,
		"buttons": wm.buttons,
		"links_webmap": wm.links,
		"forms": wm.forms,
		"inputs": wm.inputs,
		"regions": wm.regions,
		"keywords": wm.keywords,
		"ax_interactives": wm.ax_interactives,
		"raw_webmap": wm.raw,
		"all_links": links,  # full href list from DOM
		"headings": headings,
		"mobile": mobile_data,
	}


async def main():
	results = []
	async with AuthScanSession(STORAGE, headless=True) as session:
		for path in PAGES:
			url = BASE + path
			try:
				rec = await collect_for_url(session, url)
				results.append(rec)
				print(
					f"[qa] {path} done: status={rec['main_status']} "
					f"bad_resp={len(rec['bad_responses'])} "
					f"failed={len(rec['failed_requests'])} "
					f"console_err={sum(1 for m in rec['console_msgs'] if m['level']=='error')} "
					f"exceptions={len(rec['exceptions'])} "
					f"links={len(rec['all_links'])} "
					f"broken_markers={rec['broken_data_markers_found']}",
					flush=True,
				)
			except Exception as e:
				print(f"[qa] ERROR on {path}: {e}", flush=True)
				results.append({"target_url": url, "error": str(e)})
	OUT.write_text(json.dumps(results, indent=2))
	print(f"\n[qa] wrote {OUT} ({OUT.stat().st_size} bytes)", flush=True)


if __name__ == "__main__":
	asyncio.run(main())
