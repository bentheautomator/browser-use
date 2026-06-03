"""
W4 — tests for dynamic discovery + auth bootstrap classmethods + CLI.

Covers the pieces of `browser_use.qa.pipeline` and `browser_use.qa.auth`
that do not require a live browser. The end-to-end auto-discover run is
exercised by the live smoke in `examples/qa_audit/run_pipeline.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from browser_use.qa import QAPipeline
from browser_use.qa.auth import (
	write_storage_from_cookies,
	write_storage_from_localstorage,
)

# ---------------------------------------------------------------------------
# from_url
# ---------------------------------------------------------------------------


def test_from_url_splits_origin_and_path(tmp_path: Path) -> None:
	(tmp_path / 'auth.json').write_text('{"cookies": []}')
	pipeline = QAPipeline.from_url(
		'https://app.acme.com/dashboard',
		auth_path=tmp_path / 'auth.json',
		report_dir=tmp_path / 'out',
	)
	assert pipeline.base_url == 'https://app.acme.com'
	assert pipeline.target_paths == ['/dashboard']
	assert pipeline.report_dir == tmp_path / 'out'


def test_from_url_with_explicit_target_paths_overrides_inferred(tmp_path: Path) -> None:
	(tmp_path / 'auth.json').write_text('{"cookies": []}')
	pipeline = QAPipeline.from_url(
		'https://app.acme.com/dashboard',
		auth_path=tmp_path / 'auth.json',
		report_dir=tmp_path / 'out',
		target_paths=['/users', '/billing'],
	)
	# explicit list wins over the path-inferred default
	assert pipeline.target_paths == ['/users', '/billing']


def test_from_url_origin_only_defaults_to_empty_target_paths(tmp_path: Path) -> None:
	(tmp_path / 'auth.json').write_text('{"cookies": []}')
	pipeline = QAPipeline.from_url(
		'https://app.acme.com',
		auth_path=tmp_path / 'auth.json',
		report_dir=tmp_path / 'out',
	)
	# empty target_paths -> .run() will discover via sitemap
	assert pipeline.target_paths == []


# ---------------------------------------------------------------------------
# auth helpers — storage_state shape
# ---------------------------------------------------------------------------


def test_write_storage_from_cookies_writes_playwright_format(tmp_path: Path) -> None:
	path = write_storage_from_cookies(
		tmp_path / 'auth.json',
		'https://app.acme.com',
		{'session': 'abc', 'csrf': 'xyz'},
	)
	state = json.loads(path.read_text())
	assert {c['name'] for c in state['cookies']} == {'session', 'csrf'}
	assert all(c['domain'] == 'app.acme.com' for c in state['cookies'])
	assert all(c['path'] == '/' for c in state['cookies'])
	# expires=-1 marks the cookie as a session cookie in Playwright
	assert all(c['expires'] == -1 for c in state['cookies'])
	# origins kept empty unless localstorage helper is used
	assert state['origins'] == []


def test_write_storage_from_cookies_honors_explicit_domain(tmp_path: Path) -> None:
	path = write_storage_from_cookies(
		tmp_path / 'auth.json',
		'https://localhost:8080',
		{'session': 'abc'},
		domain='.acme.com',
		secure=True,
		http_only=True,
	)
	state = json.loads(path.read_text())
	cookie = state['cookies'][0]
	assert cookie['domain'] == '.acme.com'
	assert cookie['secure'] is True
	assert cookie['httpOnly'] is True


def test_write_storage_from_localstorage_emits_origin_block(tmp_path: Path) -> None:
	path = write_storage_from_localstorage(
		tmp_path / 'auth.json',
		'https://app.acme.com',
		{'accessToken': 'eyJ...', 'auth_token': 'eyJ...'},
		cookies={'csrf': 'xyz'},
	)
	state = json.loads(path.read_text())
	assert state['origins'][0]['origin'] == 'https://app.acme.com'
	keys = {entry['name'] for entry in state['origins'][0]['localStorage']}
	assert keys == {'accessToken', 'auth_token'}
	# cookies kwarg flowed through
	assert any(c['name'] == 'csrf' for c in state['cookies'])


# ---------------------------------------------------------------------------
# from_cookies + from_bearer (no live browser needed — these write storage
# and return a configured QAPipeline)
# ---------------------------------------------------------------------------


def test_from_cookies_writes_storage_and_returns_pipeline(tmp_path: Path) -> None:
	storage = tmp_path / 'auth.json'
	pipeline = QAPipeline.from_cookies(
		'https://app.acme.com',
		{'session': 'abc'},
		auth_path=storage,
		report_dir=tmp_path / 'out',
		target_paths=['/dashboard'],
	)
	assert storage.exists()
	state = json.loads(storage.read_text())
	assert state['cookies'][0]['name'] == 'session'
	assert pipeline.base_url == 'https://app.acme.com'
	assert pipeline.target_paths == ['/dashboard']
	assert pipeline.storage_state_path == storage


def test_from_bearer_defaults_to_common_keys(tmp_path: Path) -> None:
	storage = tmp_path / 'auth.json'
	pipeline = QAPipeline.from_bearer(
		'https://app.acme.com',
		'eyJtest',
		auth_path=storage,
		report_dir=tmp_path / 'out',
	)
	state = json.loads(storage.read_text())
	stored_keys = {entry['name'] for entry in state['origins'][0]['localStorage']}
	assert stored_keys == {'auth_token', 'accessToken', 'id_token', 'jwt'}
	assert pipeline.storage_state_path == storage


def test_from_bearer_honors_explicit_storage_keys(tmp_path: Path) -> None:
	storage = tmp_path / 'auth.json'
	QAPipeline.from_bearer(
		'https://app.acme.com',
		'eyJtest',
		storage_keys=['my_token'],
		auth_path=storage,
		report_dir=tmp_path / 'out',
	)
	state = json.loads(storage.read_text())
	assert [entry['name'] for entry in state['origins'][0]['localStorage']] == ['my_token']


# ---------------------------------------------------------------------------
# CLI argument parsing (no full run)
# ---------------------------------------------------------------------------


def test_cli_parser_accepts_minimal_url() -> None:
	from browser_use.qa.__main__ import _build_parser, _parse_paths

	parser = _build_parser()
	args = parser.parse_args(['https://app.acme.com'])
	assert args.url == 'https://app.acme.com'
	assert args.auth_path is None
	assert args.out_dir is None
	assert _parse_paths(args.paths) == []


def test_cli_parser_parses_cookies_and_paths() -> None:
	from browser_use.qa.__main__ import _build_parser, _parse_cookies, _parse_paths

	parser = _build_parser()
	args = parser.parse_args(
		[
			'https://x',
			'--paths',
			'/a,/b, /c ',
			'--cookies',
			'session=abc; csrf=xyz; ; broken',
		]
	)
	assert _parse_paths(args.paths) == ['/a', '/b', '/c']
	cookies = _parse_cookies(args.cookies)
	assert cookies == {'session': 'abc', 'csrf': 'xyz'}


def test_cli_parser_bearer_keys_repeatable() -> None:
	from browser_use.qa.__main__ import _build_parser

	parser = _build_parser()
	args = parser.parse_args(
		[
			'https://x',
			'--bearer',
			'eyJ',
			'--bearer-key',
			'token_a',
			'--bearer-key',
			'token_b',
		]
	)
	assert args.bearer == 'eyJ'
	assert args.bearer_keys == ['token_a', 'token_b']
