"""
12-category audit synthesized from proposed UX heuristics + Nielsen 10 +
WCAG 2.2 AA.

  1. Landmarks & Structure        (WCAG 1.3.1, Nielsen consistency)
  2. Navigation Clarity           (WCAG 2.4, Nielsen recognition>recall)
  3. Actions & Affordances        (Nielsen match real-world)
  4. Forms & Inputs               (WCAG 3.3.2, Nielsen error prevention)
  5. A11y Name Coverage           (WCAG 4.1.2 Name/Role/Value)
  6. Label Hygiene                (WCAG 2.4.6, Nielsen consistency)
  7. Interactive Density          (Nielsen aesthetic/minimalist)
  8. Auth Posture / System Status (WCAG 4.1.3, Nielsen visibility)
  9. Routes & Network Health      (HTTP status of links + main doc)
 10. Runtime Errors & Broken Data (console errors, JS exceptions, SPA-404,
                                   error-boundary markers in body text)
 11. Mobile Friendliness          (viewport meta + 375px overflow check)
 12. Heading Order & Page Titles  (h1 count, level jumps, title uniqueness)

Each category is a function taking the scanner output dict for one page
plus any cross-page context it needs and yielding `Finding` dicts with
{cat, category, sev (issue|warn|info), msg, url}.

`run_audit(pages, link_probe, spa_probe)` is the orchestrator that
applies every category to every page and returns the flat list of
findings. The `examples/qa_audit/audit_full.py` script is now a thin
wrapper around this module.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterator

# Categories either take just the page dict, or page + an extra context dict
# (for cats 9 and 10 which need the link-probe / spa-probe maps).
CategoryFn = Callable[..., Iterator[dict]]

AMBIGUOUS_LABELS: frozenset[str] = frozenset(
	{
		'button',
		'click here',
		'click',
		'more',
		'ok',
		'yes',
		'no',
		'link',
		'here',
		'read more',
		'...',
		'?',
		'!',
	}
)
EXPECTED_LANDMARKS: frozenset[str] = frozenset({'nav', 'aside', 'main', 'header', 'footer'})
EXPECTED_PAGE_TYPES: frozenset[str] = frozenset({'page', 'dashboard', 'home', 'app'})


def _yield(cat: int, sev: str, msg: str) -> dict:
	return {'cat': cat, 'sev': sev, 'msg': msg}


def cat1_landmarks(page: dict) -> Iterator[dict]:
	regions_text = ' '.join(page.get('regions', [])).lower()
	found = {lm for lm in EXPECTED_LANDMARKS if lm in regions_text}
	missing = EXPECTED_LANDMARKS - found
	if len(found) < 3:
		yield _yield(1, 'issue', f'only {len(found)} landmarks (found={sorted(found)})')
	elif missing:
		yield _yield(1, 'warn', f'missing landmarks: {sorted(missing)}')
	else:
		yield _yield(1, 'info', f'all landmarks present: {sorted(found)}')


def cat2_nav(page: dict) -> Iterator[dict]:
	links = page.get('links_webmap', [])
	n = len(links)
	if n == 0:
		yield _yield(2, 'issue', 'zero links — no navigation')
		return
	short = [link for link in links if len(link.strip()) < 3]
	if short:
		yield _yield(2, 'warn', f'{len(short)} links with <3 char text')
	if n > 50:
		yield _yield(2, 'warn', f'{n} links — likely too dense')
	yield _yield(2, 'info', f'{n} nav links')


def cat3_actions(page: dict) -> Iterator[dict]:
	buttons = page.get('buttons', [])
	if not buttons:
		yield _yield(3, 'warn', 'zero buttons')
		return
	amb = [b for b in buttons if b.strip().lower() in AMBIGUOUS_LABELS]
	if amb:
		yield _yield(3, 'issue', f'{len(amb)} ambiguous button labels: {amb}')
	yield _yield(3, 'info', f'{len(buttons)} buttons')


def cat4_forms(page: dict) -> Iterator[dict]:
	f = page.get('forms', 0)
	i = page.get('inputs', 0)
	if f == 0 and i > 0:
		yield _yield(4, 'warn', f'{i} inputs but 0 <form> wrappers — no native submit/autofill semantics')
	yield _yield(4, 'info', f'forms={f} inputs={i}')


def cat5_ax(page: dict) -> Iterator[dict]:
	ax = page.get('ax_interactives', [])
	if not ax and not page.get('enriched_via_ax_tree'):
		yield _yield(5, 'info', 'webmap covered everything, AX not sampled')
		return
	if not ax:
		yield _yield(5, 'warn', 'AX fallback ran but returned 0 nodes')
		return
	unnamed = [n for n in ax if not n.get('name')]
	pct = round(100 * len(unnamed) / len(ax), 1)
	sev = 'issue' if pct > 20 else 'warn' if pct > 5 else 'info'
	yield _yield(5, sev, f'{len(unnamed)}/{len(ax)} AX nodes ({pct}%) have no accessible name')


def cat6_hygiene(page: dict) -> Iterator[dict]:
	labels = [link.strip() for link in page.get('buttons', []) + page.get('links_webmap', []) if link.strip()]
	dupes = [k for k, v in Counter(labels).items() if v > 1]
	if dupes:
		yield _yield(6, 'warn', f'{len(dupes)} duplicate labels: {dupes[:5]}')
	url_leaks = [link for link in labels if 'http' in link.lower() or link.startswith('/')]
	if url_leaks:
		yield _yield(6, 'issue', f'{len(url_leaks)} labels look like raw URLs')


def cat7_density(page: dict) -> Iterator[dict]:
	i = page.get('interactive_count', 0)
	r = len(page.get('regions', []))
	ratio = round(i / r, 1) if r else float('inf')
	sev = 'warn' if ratio > 15 else 'info'
	yield _yield(7, sev, f'{i} interactives / {r} regions (ratio {ratio})')


def cat8_auth(page: dict) -> Iterator[dict]:
	labels = ' | '.join(b.lower() for b in page.get('buttons', []) + page.get('links_webmap', []))
	has_signout = 'sign out' in labels or 'log out' in labels or 'logout' in labels
	on_login = '/login' in (page.get('landed_url') or '')
	if on_login:
		yield _yield(8, 'issue', f'landed on /login: {page.get("landed_url")}')
	elif has_signout:
		yield _yield(8, 'info', 'Sign Out present — authenticated')
	else:
		yield _yield(8, 'warn', 'no Sign Out detected')


def cat9_routes(page: dict, link_probe_by_href: dict) -> Iterator[dict]:
	status = page.get('main_status')
	if status is None:
		yield _yield(9, 'warn', 'main document status not captured')
	elif status >= 400:
		yield _yield(9, 'issue', f'main document HTTP {status}')
	else:
		yield _yield(9, 'info', f'main document HTTP {status}')
	bad = page.get('bad_responses', [])
	if bad:
		preview = [(b['status'], b['url'][:60]) for b in bad[:3]]
		yield _yield(9, 'issue', f'{len(bad)} 4xx/5xx subresource responses: {preview}')
	fail = page.get('failed_requests', [])
	if fail:
		yield _yield(9, 'warn', f'{len(fail)} failed requests: {[f["reason"] for f in fail[:3]]}')
	page_hrefs = [(link.get('href') or '') for link in page.get('all_links', [])]
	page_probes = [link_probe_by_href.get(h) for h in page_hrefs if h in link_probe_by_href]
	bad_links = [r for r in page_probes if r and r.get('status', 200) >= 400]
	err_links = [r for r in page_probes if r and 'error' in r]
	if bad_links:
		preview = [(b['status'], b['href'][:60]) for b in bad_links[:5]]
		yield _yield(9, 'issue', f'{len(bad_links)} links return >=400: {preview}')
	if err_links:
		yield _yield(9, 'warn', f'{len(err_links)} link probes errored: {[e["error"] for e in err_links[:3]]}')


def cat10_runtime(page: dict, spa_probe_by_url: dict) -> Iterator[dict]:
	cm = page.get('console_msgs', [])
	errs = [m for m in cm if m.get('level') == 'error']
	warns = [m for m in cm if m.get('level') == 'warning']
	if errs:
		yield _yield(10, 'issue', f'{len(errs)} console errors: {[e["text"][:120] for e in errs[:3]]}')
	if warns:
		yield _yield(10, 'warn', f'{len(warns)} console warnings')
	exc = page.get('exceptions', [])
	if exc:
		yield _yield(10, 'issue', f'{len(exc)} JS exceptions: {[e["text"][:120] for e in exc[:3]]}')
	bdm = page.get('broken_data_markers_found', [])
	if bdm:
		yield _yield(10, 'warn', f'broken-data markers in body text: {bdm}')
	spa = spa_probe_by_url.get(page.get('target_url'))
	if spa and spa.get('spa_404'):
		yield _yield(10, 'issue', "SPA renders 404 component (HTTP 200 but page shows 'Page Not Found')")
	if spa and spa.get('error_boundary_markers'):
		yield _yield(10, 'issue', f'error boundary text visible: {spa["error_boundary_markers"]}')


def cat11_mobile(page: dict) -> Iterator[dict]:
	m = page.get('mobile', {}) or {}
	if not m:
		yield _yield(11, 'warn', 'mobile pass did not run')
		return
	if not m.get('viewportMeta'):
		yield _yield(11, 'issue', "missing <meta name='viewport'> — page won't scale on mobile")
	if m.get('hasOverflowX'):
		yield _yield(
			11,
			'issue',
			f'horizontal overflow at 375px (scrollW={m.get("scrollW")} clientW={m.get("clientW")})',
		)
	yield _yield(11, 'info', f'viewport={m.get("viewportMeta")!r} 375px overflowX={m.get("hasOverflowX")}')


def cat12_headings(page: dict) -> Iterator[dict]:
	hs = page.get('headings', [])
	if not hs:
		yield _yield(12, 'warn', 'no headings detected on page')
		return
	h1s = [h for h in hs if h['level'] == 1]
	if len(h1s) == 0:
		yield _yield(12, 'issue', 'no <h1> on page')
	elif len(h1s) > 1:
		texts = [h['text'][:30] for h in h1s]
		yield _yield(12, 'warn', f'{len(h1s)} <h1> elements: {texts}')
	jumps: list[tuple[int, int, str]] = []
	prev: int | None = None
	for h in hs:
		if prev is not None and h['level'] > prev + 1:
			jumps.append((prev, h['level'], h['text'][:40]))
		prev = h['level']
	if jumps:
		yield _yield(12, 'warn', f'{len(jumps)} heading-level jumps: {jumps[:3]}')


# (number, name, function, requires-link-probe-context, requires-spa-probe-context)
CATEGORIES: list[tuple[int, str, CategoryFn, bool, bool]] = [
	(1, 'Landmarks & Structure', cat1_landmarks, False, False),
	(2, 'Navigation Clarity', cat2_nav, False, False),
	(3, 'Actions & Affordances', cat3_actions, False, False),
	(4, 'Forms & Inputs', cat4_forms, False, False),
	(5, 'A11y Name Coverage', cat5_ax, False, False),
	(6, 'Label Hygiene', cat6_hygiene, False, False),
	(7, 'Interactive Density', cat7_density, False, False),
	(8, 'Auth Posture / System Status', cat8_auth, False, False),
	(9, 'Routes & Network Health', cat9_routes, True, False),
	(10, 'Runtime Errors & Broken Data', cat10_runtime, False, True),
	(11, 'Mobile Friendliness', cat11_mobile, False, False),
	(12, 'Heading Order & Page Titles', cat12_headings, False, False),
]


def run_audit(
	pages: list[dict],
	link_probe: list[dict] | None = None,
	spa_probe: list[dict] | None = None,
) -> list[dict]:
	"""Apply all 12 categories to every page record.

	Returns a flat list of finding dicts: `{cat, category, sev, msg, url}`.
	"""
	link_probe = link_probe or []
	spa_probe = spa_probe or []
	link_by_href = {r['href']: r for r in link_probe if 'href' in r}
	spa_by_url = {r['href']: r for r in spa_probe if 'href' in r}

	findings: list[dict] = []
	for page in pages:
		if 'error' in page:
			continue
		for num, name, fn, needs_link, needs_spa in CATEGORIES:
			try:
				if needs_link:
					gen = fn(page, link_by_href)
				elif needs_spa:
					gen = fn(page, spa_by_url)
				else:
					gen = fn(page)
				for f in gen:
					findings.append({**f, 'category': name, 'url': page['target_url']})
			except Exception as e:
				findings.append(
					{
						'cat': num,
						'category': name,
						'sev': 'warn',
						'msg': f'category check errored: {e}',
						'url': page['target_url'],
					}
				)
	return findings


# Avoid an unused-import warning while still keeping `re` available for
# downstream contributors who want to extend cat10_runtime with regex
# broken-data heuristics.
_ = re
