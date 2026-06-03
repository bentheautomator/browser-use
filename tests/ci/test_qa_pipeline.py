"""
W3 — pytest coverage for `browser_use.qa`.

Focuses on the parameterizable, deterministic surface that does not
require a live browser:

  - `QAPipeline` instantiation + helper accessors
  - URL → route template normalization (`route_template_from_url`,
    `normalize_path`)
  - 12-category audit `run_audit` against synthetic page records, with
    at least two distinct categories exercised
  - Markdown / JSON report rendering against synthetic data
  - `probe_links_http` against `pytest-httpserver` (covers 200, 404, and
    a redirect chain)

The end-to-end pipeline (real BrowserSession + real `webmap` CLI +
mobile viewport) is covered by the live smoke test in
`examples/qa_audit/run_pipeline.py`, not here, because pytest CI does
not run an authenticated SPA.
"""

from __future__ import annotations

import json
from pathlib import Path

from browser_use.qa import (
	CATEGORIES,
	QAPipeline,
	render_combined_json,
	render_markdown,
	route_template_from_url,
	run_audit,
)
from browser_use.qa.sitemap import normalize_path

# ---------------------------------------------------------------------------
# QAPipeline instantiation
# ---------------------------------------------------------------------------


def test_pipeline_constructs_with_required_fields(tmp_path: Path) -> None:
	storage = tmp_path / 'state.json'
	storage.write_text('{"cookies": [], "origins": []}')
	pipeline = QAPipeline(
		base_url='http://localhost:8080',
		storage_state_path=storage,
		target_paths=['/dashboard'],
		report_dir=tmp_path / 'out',
	)
	assert pipeline.base_url == 'http://localhost:8080'
	assert pipeline.target_paths == ['/dashboard']
	# defaults survive
	assert pipeline.hydration_sec == 5.0
	assert pipeline.mobile_viewport == (375, 812)
	assert pipeline.max_crawl_pages == 30
	assert pipeline.max_crawl_depth == 2


def test_pipeline_seed_urls_joins_base_and_paths(tmp_path: Path) -> None:
	storage = tmp_path / 'state.json'
	storage.write_text('{"cookies": []}')
	pipeline = QAPipeline(
		base_url='http://localhost:8080',
		storage_state_path=storage,
		target_paths=['/a', '/b', 'http://other.test/c'],
	)
	seeds = pipeline._seed_urls()
	assert seeds == [
		'http://localhost:8080/a',
		'http://localhost:8080/b',
		'http://other.test/c',
	]


def test_pipeline_loads_cookies_from_storage_state(tmp_path: Path) -> None:
	storage = tmp_path / 'state.json'
	storage.write_text(
		json.dumps(
			{
				'cookies': [
					{'name': 'session', 'value': 'abc'},
					{'name': 'csrf', 'value': 'xyz'},
				],
			}
		)
	)
	pipeline = QAPipeline(base_url='http://x', storage_state_path=storage)
	cookies = pipeline._load_cookies()
	assert cookies == {'session': 'abc', 'csrf': 'xyz'}


def test_pipeline_load_cookies_handles_missing_file(tmp_path: Path) -> None:
	pipeline = QAPipeline(base_url='http://x', storage_state_path=tmp_path / 'does-not-exist.json')
	assert pipeline._load_cookies() == {}


# ---------------------------------------------------------------------------
# URL → route template
# ---------------------------------------------------------------------------


def test_route_template_replaces_tenant_slug() -> None:
	assert route_template_from_url('http://x/t/on-plane/dashboard') == '/t/[slug]/dashboard'


def test_route_template_replaces_uuid() -> None:
	tpl = route_template_from_url('http://x/users/550e8400-e29b-41d4-a716-446655440000')
	assert tpl == '/users/[uuid]'


def test_route_template_replaces_numeric_ids() -> None:
	assert route_template_from_url('http://x/orders/12345') == '/orders/[id]'


def test_route_template_replaces_long_hex_ids() -> None:
	# 16+ hex chars qualify as HEX
	assert route_template_from_url('http://x/items/abcdef1234567890') == '/items/[hex]'


def test_normalize_path_combines_all_replacements() -> None:
	# uuid + numeric + plain slug in one path. `42` is only 2 digits so
	# NUM_RE (which requires 3+ digits to avoid catching small enums)
	# leaves it alone — use a 3-digit number to confirm replacement.
	path = '/t/myws/items/550e8400-e29b-41d4-a716-446655440000/revisions/4242'
	assert normalize_path(path) == '/t/myws/items/[uuid]/revisions/[id]'


# ---------------------------------------------------------------------------
# 12-category audit
# ---------------------------------------------------------------------------


def _synthetic_page(**overrides) -> dict:
	"""Build a deliberately under-specified page record so individual
	tests can override exactly the fields they exercise."""
	base = {
		'target_url': 'http://x/dashboard',
		'landed_url': 'http://x/dashboard',
		'main_status': 200,
		'bad_responses': [],
		'failed_requests': [],
		'console_msgs': [],
		'exceptions': [],
		'broken_data_markers_found': [],
		'buttons': ['Save', 'Cancel'],
		'links_webmap': ['Home', 'Settings'],
		'all_links': [],
		'forms': 1,
		'inputs': 3,
		'regions': ['nav', 'main', 'header', 'aside'],
		'keywords': [],
		'interactive_count': 5,
		'enriched_via_ax_tree': False,
		'ax_interactives': [],
		'page_type': 'page',
		'page_purpose': 'show dashboard',
		'headings': [{'level': 1, 'text': 'Dashboard'}, {'level': 2, 'text': 'Overview'}],
		'mobile': {
			'scrollW': 375,
			'clientW': 375,
			'hasOverflowX': False,
			'viewportMeta': 'width=device-width, initial-scale=1.0',
		},
	}
	base.update(overrides)
	return base


def test_run_audit_emits_findings_for_each_category() -> None:
	pages = [_synthetic_page()]
	findings = run_audit(pages)
	# at least one finding per category (cat 5 emits a single info when AX
	# wasn't sampled, cat 6 emits nothing when there are no dupes — verify
	# explicit categories that always fire)
	cats_with_findings = {f['cat'] for f in findings}
	# cats 1, 2, 3, 4, 5, 7, 8, 9, 11 always fire on a normal page
	expected_always = {1, 2, 3, 4, 5, 7, 8, 9, 11}
	missing = expected_always - cats_with_findings
	assert not missing, f'missing always-firing categories: {missing}'


def test_cat4_warns_when_inputs_present_without_form() -> None:
	page = _synthetic_page(forms=0, inputs=20)
	findings = run_audit([page])
	cat4_warns = [f for f in findings if f['cat'] == 4 and f['sev'] == 'warn']
	assert len(cat4_warns) == 1
	assert '<form>' in cat4_warns[0]['msg']


def test_cat8_flags_login_landed_url() -> None:
	page = _synthetic_page(landed_url='http://x/login', buttons=[], links_webmap=[])
	findings = run_audit([page])
	cat8_issues = [f for f in findings if f['cat'] == 8 and f['sev'] == 'issue']
	assert any('/login' in f['msg'] for f in cat8_issues)


def test_cat10_detects_spa_404_from_probe() -> None:
	page = _synthetic_page()
	spa_probe = [{'href': page['target_url'], 'spa_404': True}]
	findings = run_audit([page], spa_probe=spa_probe)
	cat10_issues = [f for f in findings if f['cat'] == 10 and f['sev'] == 'issue']
	assert any('404' in f['msg'] for f in cat10_issues)


def test_cat12_flags_multiple_h1_elements() -> None:
	# two h1s on one page (e.g. workspace shell + embedded sub-page) is a
	# classic SPA-router-misconfig fingerprint
	page = _synthetic_page(
		headings=[
			{'level': 1, 'text': 'Command Center'},
			{'level': 1, 'text': '404'},
		]
	)
	findings = run_audit([page])
	cat12_warns = [f for f in findings if f['cat'] == 12 and f['sev'] == 'warn']
	assert any('<h1>' in f['msg'] for f in cat12_warns)


def test_run_audit_skips_pages_with_error_key() -> None:
	pages = [_synthetic_page(), {'target_url': 'http://x/broken', 'error': 'navigate timed out'}]
	findings = run_audit(pages)
	# good page gets findings; broken page should not appear
	urls = {f['url'] for f in findings}
	assert urls == {'http://x/dashboard'}


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def test_render_markdown_includes_critical_section_when_issues_exist() -> None:
	page = _synthetic_page(landed_url='http://x/login', buttons=[], links_webmap=[])
	findings = run_audit([page])
	md = render_markdown([page], [], [], findings)
	assert '🔴 Critical Issues' in md
	assert '/login' in md


def test_render_markdown_executive_summary_counts() -> None:
	page = _synthetic_page()
	findings = run_audit([page])
	md = render_markdown([page], [], [], findings, target_label='unit-test target')
	assert '**1** pages audited' in md
	assert 'unit-test target' in md


def test_render_combined_json_carries_all_artifacts() -> None:
	page = _synthetic_page()
	findings = run_audit([page])
	combined = render_combined_json([page], [], [], findings, target_label='unit-test target')
	assert combined['target'] == 'unit-test target'
	assert combined['rollup']['pages'] == 1
	assert combined['rollup']['findings'] == len(findings)
	# every cat key present even if empty
	for c in range(1, 13):
		assert f'cat{c}' in combined['rollup']['by_category']


# ---------------------------------------------------------------------------
# Categories registry shape
# ---------------------------------------------------------------------------


def test_categories_registry_has_12_entries() -> None:
	assert len(CATEGORIES) == 12
	numbers = [c[0] for c in CATEGORIES]
	assert numbers == list(range(1, 13))


def test_categories_registry_flags_link_probe_dependents() -> None:
	# cat 9 needs link_probe context; cat 10 needs spa_probe context
	for num, _name, _fn, needs_link, needs_spa in CATEGORIES:
		if num == 9:
			assert needs_link is True and needs_spa is False
		elif num == 10:
			assert needs_link is False and needs_spa is True
		else:
			assert needs_link is False and needs_spa is False


# ---------------------------------------------------------------------------
# HTTP probe (real network fixture via pytest-httpserver)
# ---------------------------------------------------------------------------


def test_probe_links_http_handles_200_404_and_redirect(httpserver) -> None:
	import asyncio

	from browser_use.qa._probes import probe_links_http

	httpserver.expect_request('/ok').respond_with_data('hi', status=200)
	httpserver.expect_request('/missing').respond_with_data('gone', status=404)
	httpserver.expect_request('/redirect').respond_with_data('', status=302, headers={'Location': httpserver.url_for('/ok')})

	hrefs = [
		httpserver.url_for('/ok'),
		httpserver.url_for('/missing'),
		httpserver.url_for('/redirect'),
		'javascript:void(0)',  # should be skipped
	]
	results = asyncio.run(probe_links_http(hrefs))
	by_href = {r['href']: r for r in results}
	assert by_href[hrefs[0]]['status'] == 200
	assert by_href[hrefs[1]]['status'] == 404
	# redirect probe should follow to the final 200; redirect chain captured
	assert by_href[hrefs[2]]['status'] == 200
	assert len(by_href[hrefs[2]]['redirect_chain']) == 1
	# non-http scheme skipped
	assert 'skipped' in by_href['javascript:void(0)']
