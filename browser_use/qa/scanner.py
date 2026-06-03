"""
Page scanner — deep per-URL capture composing webmap + AX + CDP listeners.

The scanner attaches Network + Runtime event listeners to a live
BrowserSession's CDP client BEFORE navigation, drives the navigation,
waits for SPA hydration, then collects:

  - main document HTTP status + 4xx/5xx subresource responses
  - failed requests
  - console messages (error/warning levels only)
  - JS uncaught exceptions
  - webmap scan via WebmapExtractor (shared-browser --stdin mode)
  - DOM-extracted link list (every <a href>) for sitemap / link-probe stages
  - body-text search for "broken data" markers (undefined, NaN, error
    strings) — catches the UI surface of broken-data bugs
  - heading tree from `document.querySelectorAll('h1,h2,h3,h4,h5,h6')` for
    heading-order audit + SPA-404 detection
  - mobile-viewport overflow + viewport-meta presence (Emulation API)

Output: a single dict shaped like the records the audit + report layers
consume (see `examples/qa_audit/qa_scan.py` for the original concrete
form; this module is the parameterized rewrite).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from browser_use.integrations.webmap import WebmapExtractor

if TYPE_CHECKING:
	from browser_use.browser.session import BrowserSession

logger = logging.getLogger(__name__)


DEFAULT_BROKEN_DATA_MARKERS: tuple[str, ...] = (
	'undefined',
	'[object Object]',
	'NaN',
	'Error:',
	'TypeError',
	'ReferenceError',
	'500 Internal',
	'404 Not Found',
	'Failed to fetch',
	'Application error',
	'Something went wrong',
)


async def scan_page(
	session: BrowserSession,
	url: str,
	*,
	base_url: str | None = None,
	hydration_sec: float = 5.0,
	mobile_viewport: tuple[int, int] = (375, 812),
	mobile_render_sec: float = 2.0,
	broken_data_markers: tuple[str, ...] = DEFAULT_BROKEN_DATA_MARKERS,
	webmap_extractor: WebmapExtractor | None = None,
) -> dict:
	"""
	Deep-scan a single URL inside an already-authenticated BrowserSession.

	Args:
	    session: a live, authenticated BrowserSession (typically from
	        `browser_use.integrations.webmap.AuthScanSession`).
	    url: absolute URL to scan.
	    base_url: same-origin prefix used to classify Document responses as
	        the page's "main" status. Defaults to the origin of `url`.
	    hydration_sec: seconds to wait after navigation before capturing.
	    mobile_viewport: (width, height) for the post-scan mobile pass.
	    mobile_render_sec: seconds to wait after applying mobile viewport.
	    broken_data_markers: substrings to scan body text for.
	    webmap_extractor: optional pre-constructed WebmapExtractor; one is
	        created if omitted.
	"""
	if base_url is None:
		# strip path + query, keep scheme://host
		from urllib.parse import urlparse

		p = urlparse(url)
		base_url = f'{p.scheme}://{p.netloc}'

	cdp = await session.get_or_create_cdp_session(target_id=None, focus=False)
	sid = cdp.session_id

	# capture buffers
	main_status: dict = {'code': None, 'url': None}
	bad_responses: list[dict] = []
	failed_requests: list[dict] = []
	console_msgs: list[dict] = []
	exceptions: list[dict] = []

	def on_response(p, session_id=None):
		try:
			r = p.get('response', {})
			status = r.get('status', 0)
			rurl = r.get('url', '')
			if main_status['code'] is None and rurl.startswith(base_url):
				if p.get('type') == 'Document':
					main_status['code'] = status
					main_status['url'] = rurl
			if 400 <= status < 600:
				bad_responses.append({'status': status, 'url': rurl, 'type': p.get('type')})
		except Exception:
			pass

	def on_loading_failed(p, session_id=None):
		failed_requests.append(
			{
				'url': p.get('url') or p.get('documentURL') or '',
				'reason': p.get('errorText', ''),
				'type': p.get('type'),
			}
		)

	def on_console(p, session_id=None):
		try:
			level = p.get('type', '')
			if level not in ('error', 'warning'):
				return
			args = p.get('args', []) or []
			texts = []
			for a in args:
				v = a.get('value')
				if v is None:
					v = a.get('description') or a.get('preview', {}).get('description', '')
				texts.append(str(v))
			console_msgs.append({'level': level, 'text': ' '.join(texts)[:500]})
		except Exception:
			pass

	def on_exception(p, session_id=None):
		try:
			d = p.get('exceptionDetails', {})
			exceptions.append(
				{
					'text': d.get('text', ''),
					'url': d.get('url', ''),
					'line': d.get('lineNumber'),
					'exception': (d.get('exception') or {}).get('description', '')[:500],
				}
			)
		except Exception:
			pass

	await cdp.cdp_client.send.Network.enable(session_id=sid)
	await cdp.cdp_client.send.Runtime.enable(session_id=sid)
	cdp.cdp_client.register.Network.responseReceived(on_response)
	cdp.cdp_client.register.Network.loadingFailed(on_loading_failed)
	cdp.cdp_client.register.Runtime.consoleAPICalled(on_console)
	cdp.cdp_client.register.Runtime.exceptionThrown(on_exception)

	await session._cdp_navigate(url)
	await asyncio.sleep(hydration_sec)
	landed = await session.get_current_page_url()

	extractor = webmap_extractor or WebmapExtractor()
	wm = await asyncio.wait_for(extractor.scan_current_page(session), timeout=45)

	# raw HTML for broken-data scan + downstream
	html = await extractor._get_page_content(session) or ''

	# enumerate links via JS
	link_eval = await cdp.cdp_client.send.Runtime.evaluate(
		params={
			'expression': (
				"Array.from(document.querySelectorAll('a[href]')).map(a => ({"
				"href: a.href, text: (a.innerText || a.textContent || '').trim().slice(0, 80)"
				'}))'
			),
			'returnByValue': True,
		},
		session_id=sid,
	)
	all_links: list[dict] = []
	try:
		all_links = link_eval.get('result', {}).get('value', []) or []
	except Exception:
		pass

	# body text broken-data scan
	text_eval = await cdp.cdp_client.send.Runtime.evaluate(
		params={'expression': "document.body && document.body.innerText || ''", 'returnByValue': True},
		session_id=sid,
	)
	body_text = (text_eval.get('result', {}) or {}).get('value', '') or ''
	broken_markers = [m for m in broken_data_markers if m.lower() in body_text.lower()]

	# mobile-viewport pass
	mw, mh = mobile_viewport
	await cdp.cdp_client.send.Emulation.setDeviceMetricsOverride(
		params={'width': mw, 'height': mh, 'deviceScaleFactor': 2, 'mobile': True},
		session_id=sid,
	)
	await asyncio.sleep(mobile_render_sec)
	mobile_eval = await cdp.cdp_client.send.Runtime.evaluate(
		params={
			'expression': (
				'({'
				'scrollW: document.documentElement.scrollWidth,'
				'clientW: document.documentElement.clientWidth,'
				'hasOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,'
				'viewportMeta: (document.querySelector(\'meta[name="viewport"]\') || {}).content || null,'
				"visibleNavs: document.querySelectorAll('nav:not([hidden])').length,"
				'hiddenAsides: document.querySelectorAll(\'aside[hidden], aside[aria-hidden="true"]\').length'
				'})'
			),
			'returnByValue': True,
		},
		session_id=sid,
	)
	mobile_data = (mobile_eval.get('result', {}) or {}).get('value', {}) or {}

	await cdp.cdp_client.send.Emulation.clearDeviceMetricsOverride(session_id=sid)

	# heading hierarchy for cat-12 audit + SPA-404 detection
	heading_eval = await cdp.cdp_client.send.Runtime.evaluate(
		params={
			'expression': (
				"Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))"
				".map(h => ({level: parseInt(h.tagName[1]), text: (h.textContent || '').trim().slice(0, 100)}))"
			),
			'returnByValue': True,
		},
		session_id=sid,
	)
	headings = (heading_eval.get('result', {}) or {}).get('value', []) or []

	await cdp.cdp_client.send.Network.disable(session_id=sid)
	await cdp.cdp_client.send.Runtime.disable(session_id=sid)

	return {
		'target_url': url,
		'landed_url': landed,
		'main_status': main_status['code'],
		'main_status_url': main_status['url'],
		'bad_responses': bad_responses,
		'failed_requests': failed_requests,
		'console_msgs': console_msgs,
		'exceptions': exceptions,
		'broken_data_markers_found': broken_markers,
		'page_type': wm.page_type,
		'page_purpose': wm.page_purpose,
		'interactive_count': wm.interactive_count,
		'enriched_via_ax_tree': wm.enriched_via_ax_tree,
		'buttons': wm.buttons,
		'links_webmap': wm.links,
		'forms': wm.forms,
		'inputs': wm.inputs,
		'regions': wm.regions,
		'keywords': wm.keywords,
		'ax_interactives': wm.ax_interactives,
		'raw_webmap': wm.raw,
		'all_links': all_links,
		'headings': headings,
		'mobile': mobile_data,
		'page_html': html,
	}


async def scan_pages(
	session: BrowserSession,
	urls: list[str],
	**kwargs,
) -> list[dict]:
	"""Scan a list of URLs sequentially in a single session."""
	results: list[dict] = []
	for url in urls:
		try:
			results.append(await scan_page(session, url, **kwargs))
		except Exception as e:
			logger.warning(f'scan_page failed for {url}: {e}')
			results.append({'target_url': url, 'error': str(e)})
	return results
