"""
Sitemap discovery — URLs + route templates + API inventory via BFS crawl.

Visits each seed URL inside an authenticated BrowserSession, captures all
outbound `<a href>` and all XHR/fetch network requests, then fans out to
same-origin discovered links up to `max_depth` / `max_pages`.

Normalizes URL paths to route templates by:
  - replacing the workspace slug after `/t/` with `[slug]`
  - replacing UUIDs, long hex IDs, and numeric IDs in any segment with
    `[uuid]` / `[hex]` / `[id]`

Output shape:
  {
    "pages": [{url, landed_url, depth, parent, outbound_links, api_calls}],
    "route_templates": [{template, instances, instance_count}],
    "api_endpoints": [{method, path_template, statuses, called_from_pages,
                       sample_urls, total_calls}],
  }
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
	from browser_use.browser.session import BrowserSession

logger = logging.getLogger(__name__)


UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
HEX_RE = re.compile(r'\b[0-9a-f]{16,}\b', re.I)
NUM_RE = re.compile(r'\b\d{3,}\b')


def normalize_path(path: str) -> str:
	"""Replace id-like segments in a URL path with template placeholders."""
	p = UUID_RE.sub('[uuid]', path)
	p = HEX_RE.sub('[hex]', p)
	p = NUM_RE.sub('[id]', p)
	return p


def route_template_from_url(url: str) -> str:
	"""Convert a concrete URL to a route template.

	Workspace slug after /t/ becomes [slug] so all tenants collapse to one
	template; id-like segments elsewhere are normalized via `normalize_path`.
	"""
	parsed = urlparse(url)
	parts = parsed.path.split('/')
	if len(parts) >= 3 and parts[1] == 't':
		parts[2] = '[slug]'
	return normalize_path('/'.join(parts))


def _is_same_origin(url: str, base: str) -> bool:
	return urlparse(url).netloc == urlparse(base).netloc


async def _crawl_one(session: BrowserSession, url: str) -> dict:
	"""Visit one URL, capture outbound links + XHR/fetch API calls."""
	cdp = await session.get_or_create_cdp_session(target_id=None, focus=False)
	sid = cdp.session_id

	api_calls: list[dict] = []
	in_flight: dict[str, dict] = {}

	def on_request_will_be_sent(p, session_id=None):
		try:
			rid = p.get('requestId')
			req = p.get('request') or {}
			rurl = req.get('url') or ''
			rtype = p.get('type', '')
			if rtype in ('XHR', 'Fetch'):
				in_flight[rid] = {'url': rurl, 'method': req.get('method', 'GET')}
		except Exception:
			pass

	def on_response(p, session_id=None):
		try:
			rid = p.get('requestId')
			if rid in in_flight:
				r = p.get('response') or {}
				rec = in_flight.pop(rid)
				rec['status'] = r.get('status')
				rec['mime'] = r.get('mimeType', '')
				api_calls.append(rec)
		except Exception:
			pass

	await cdp.cdp_client.send.Network.enable(session_id=sid)
	await cdp.cdp_client.send.Runtime.enable(session_id=sid)
	cdp.cdp_client.register.Network.requestWillBeSent(on_request_will_be_sent)
	cdp.cdp_client.register.Network.responseReceived(on_response)

	await session._cdp_navigate(url)
	await asyncio.sleep(4.0)
	landed = await session.get_current_page_url()

	links_eval = await cdp.cdp_client.send.Runtime.evaluate(
		params={
			'expression': (
				"Array.from(document.querySelectorAll('a[href]'))"
				".map(a => a.href).filter(h => h && !h.startsWith('javascript:') && !h.startsWith('mailto:'))"
			),
			'returnByValue': True,
		},
		session_id=sid,
	)
	out_links = (links_eval.get('result', {}) or {}).get('value', []) or []

	await cdp.cdp_client.send.Network.disable(session_id=sid)
	await cdp.cdp_client.send.Runtime.disable(session_id=sid)

	return {
		'url': url,
		'landed_url': landed,
		'outbound_links': sorted(set(out_links)),
		'api_calls': api_calls,
	}


async def crawl_sitemap(
	session: BrowserSession,
	seeds: list[str],
	*,
	base_url: str | None = None,
	max_depth: int = 2,
	max_pages: int = 30,
) -> dict:
	"""BFS-crawl `seeds` inside the authenticated `session`.

	Args:
	    session: live authenticated BrowserSession.
	    seeds: absolute seed URLs.
	    base_url: same-origin gate. Defaults to the first seed's origin.
	    max_depth: BFS depth cap.
	    max_pages: hard cap on visited URLs.

	Returns the {pages, route_templates, api_endpoints} dict described in the
	module docstring.
	"""
	if not seeds:
		return {'pages': [], 'route_templates': [], 'api_endpoints': []}
	if base_url is None:
		p = urlparse(seeds[0])
		base_url = f'{p.scheme}://{p.netloc}'

	def canon(u: str) -> str:
		return u.split('#')[0]

	visited: set[str] = set()
	queue: list[tuple[str, int, str | None]] = [(s, 0, None) for s in seeds]
	pages: list[dict] = []
	depth_map: dict[str, int] = {}
	parent_map: dict[str, str | None] = {}

	while queue and len(visited) < max_pages:
		url, depth, parent = queue.pop(0)
		cu = canon(url)
		if cu in visited:
			continue
		if not _is_same_origin(cu, base_url):
			continue
		if depth > max_depth:
			continue
		visited.add(cu)
		depth_map[cu] = depth
		parent_map[cu] = parent
		try:
			rec = await _crawl_one(session, cu)
		except Exception as e:
			logger.warning(f'crawl_one failed for {cu}: {e}')
			continue
		rec['depth'] = depth
		rec['parent'] = parent
		pages.append(rec)
		for href in rec['outbound_links']:
			ch = canon(href)
			if ch not in visited and _is_same_origin(ch, base_url):
				queue.append((ch, depth + 1, cu))

	# route templates
	templates: dict[str, list[str]] = defaultdict(list)
	for url in sorted(visited):
		templates[route_template_from_url(url)].append(url)
	route_records = [{'template': tpl, 'instances': urls, 'instance_count': len(urls)} for tpl, urls in sorted(templates.items())]

	# API inventory
	api_groups: dict[tuple[str, str], dict] = {}
	for page in pages:
		for call in page.get('api_calls', []):
			method = call['method']
			tpl = route_template_from_url(call['url'])
			key = (method, tpl)
			rec = api_groups.setdefault(
				key,
				{
					'method': method,
					'path_template': tpl,
					'statuses': defaultdict(int),
					'called_from_pages': set(),
					'sample_urls': set(),
				},
			)
			rec['statuses'][call.get('status', 0)] += 1
			rec['called_from_pages'].add(page['url'])
			if len(rec['sample_urls']) < 5:
				rec['sample_urls'].add(call['url'])
	api_records = []
	for rec in api_groups.values():
		api_records.append(
			{
				'method': rec['method'],
				'path_template': rec['path_template'],
				'statuses': dict(rec['statuses']),
				'called_from_pages': sorted(rec['called_from_pages']),
				'sample_urls': sorted(rec['sample_urls']),
				'total_calls': sum(rec['statuses'].values()),
			}
		)
	api_records.sort(key=lambda r: (-r['total_calls'], r['path_template']))

	return {'pages': pages, 'route_templates': route_records, 'api_endpoints': api_records}
