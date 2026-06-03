"""
Probes — HTTP HEAD probe of all hrefs + SPA render probe of same-origin links.

`probe_links_http` — given a set of hrefs and optional cookies, run a
bounded-concurrency HEAD probe via httpx (GET fallback on 405). Returns
per-href status + redirect chain + final URL. Skips non-http schemes
and empty netlocs.

`probe_spa_routes` — given a live authenticated BrowserSession and a set
of same-origin URLs, navigate each one and capture heading tree + title +
SPA-404 markers + error-boundary markers. Catches client-side 404s that
HEAD probes miss because the server-side route returns 200 + serves a
SPA shell that decides to render its 404 component.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
	from browser_use.browser.session import BrowserSession

logger = logging.getLogger(__name__)


DEFAULT_NOT_FOUND_MARKERS: tuple[str, ...] = (
	'404',
	'Page Not Found',
	'page not found',
	'Not Found',
	'This page could not be found',
)
DEFAULT_ERROR_BOUNDARY_MARKERS: tuple[str, ...] = (
	'Something went wrong',
	'Application error',
	'An error occurred',
	'Failed to load',
	'Unable to load',
)


async def probe_links_http(
	hrefs: list[str],
	*,
	cookies: dict[str, str] | None = None,
	concurrency: int = 10,
	timeout: float = 10.0,
	user_agent: str = 'qa-audit/1.0 (+browser-use)',
) -> list[dict]:
	"""Bounded-concurrency HEAD probe of `hrefs`.

	Returns a list of dicts with one of:
	  {href, probe_url, status, final_url, redirect_chain}  on HTTP success
	  {href, error}                                          on connect/timeout
	  {href, skipped}                                        on non-http scheme

	Requires httpx (declared in pyproject.toml `[project.optional-dependencies] qa`).
	"""
	import httpx  # imported here so the rest of `browser_use.qa` works
	# without the `qa` extra installed (relevant for category-only consumers).

	sem = asyncio.Semaphore(concurrency)

	async def probe(client: httpx.AsyncClient, href: str) -> dict:
		parsed = urlparse(href)
		if parsed.scheme not in ('http', 'https'):
			return {'href': href, 'skipped': 'non-http scheme'}
		if not parsed.netloc:
			return {'href': href, 'skipped': 'empty netloc'}
		probe_url = href.split('#')[0]
		chain: list[dict] = []
		try:
			r = await client.head(probe_url, timeout=timeout)
			if r.status_code == 405:
				r = await client.get(probe_url, timeout=timeout)
			for h in r.history:
				chain.append({'status': h.status_code, 'location': str(h.url)})
			return {
				'href': href,
				'probe_url': probe_url,
				'status': r.status_code,
				'final_url': str(r.url),
				'redirect_chain': chain,
			}
		except httpx.TimeoutException:
			return {'href': href, 'error': 'timeout'}
		except httpx.ConnectError as e:
			return {'href': href, 'error': f'connect: {e}'}
		except Exception as e:
			return {'href': href, 'error': str(e)[:200]}

	async with httpx.AsyncClient(
		follow_redirects=True,
		cookies=cookies or {},
		headers={'User-Agent': user_agent},
	) as client:

		async def bounded(h: str) -> dict:
			async with sem:
				return await probe(client, h)

		return await asyncio.gather(*[bounded(h) for h in hrefs])


async def probe_spa_routes(
	session: BrowserSession,
	hrefs: list[str],
	*,
	hydration_sec: float = 3.0,
	not_found_markers: tuple[str, ...] = DEFAULT_NOT_FOUND_MARKERS,
	error_boundary_markers: tuple[str, ...] = DEFAULT_ERROR_BOUNDARY_MARKERS,
) -> list[dict]:
	"""Navigate each href via the live authenticated session and capture
	per-route signals that a server-side HEAD probe cannot see.

	Returns a list of dicts:
	  {href, probe_url, landed_url, title, headings,
	   not_found_markers, error_boundary_markers, spa_404}
	or {href, error} on per-route failure.
	"""
	cdp = await session.get_or_create_cdp_session(target_id=None, focus=False)
	sid = cdp.session_id
	await cdp.cdp_client.send.Runtime.enable(session_id=sid)

	results: list[dict] = []
	for href in hrefs:
		probe_url = href.split('#')[0]
		out: dict = {'href': href, 'probe_url': probe_url}
		try:
			await session._cdp_navigate(probe_url)
			await asyncio.sleep(hydration_sec)
			out['landed_url'] = await session.get_current_page_url()
			h_eval = await cdp.cdp_client.send.Runtime.evaluate(
				params={
					'expression': (
						"Array.from(document.querySelectorAll('h1,h2,h3'))"
						".map(h => ({level: parseInt(h.tagName[1]), text: (h.textContent || '').trim().slice(0, 80)}))"
					),
					'returnByValue': True,
				},
				session_id=sid,
			)
			headings = (h_eval.get('result', {}) or {}).get('value', []) or []
			out['headings'] = headings
			t_eval = await cdp.cdp_client.send.Runtime.evaluate(
				params={'expression': "document.title || ''", 'returnByValue': True},
				session_id=sid,
			)
			out['title'] = (t_eval.get('result', {}) or {}).get('value', '')
			b_eval = await cdp.cdp_client.send.Runtime.evaluate(
				params={'expression': "document.body && document.body.innerText.slice(0, 5000) || ''", 'returnByValue': True},
				session_id=sid,
			)
			body = (b_eval.get('result', {}) or {}).get('value', '') or ''
			out['not_found_markers'] = [m for m in not_found_markers if m in body]
			out['error_boundary_markers'] = [m for m in error_boundary_markers if m in body]
			heading_text = ' '.join(h['text'] for h in headings).lower()
			out['spa_404'] = '404' in heading_text or 'page not found' in heading_text
		except Exception as e:
			out['error'] = str(e)[:200]
		results.append(out)

	await cdp.cdp_client.send.Runtime.disable(session_id=sid)
	return results
