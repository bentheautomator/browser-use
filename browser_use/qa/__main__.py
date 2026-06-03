"""
CLI entrypoint for `browser_use.qa`.

Installed as `qa-audit` via `[project.scripts]` in pyproject.toml.

Usage:

    qa-audit https://app.acme.com
    qa-audit https://app.acme.com --auth tmp/auth.json --out qa-out
    qa-audit https://app.acme.com --paths /dashboard,/users,/billing
    qa-audit https://app.acme.com --bearer eyJ... --bearer-key accessToken
    qa-audit https://app.acme.com --cookies 'session=abc;csrf=xyz'
    qa-audit https://app.acme.com --form-email me@x.com --form-password "$PW"

Exit codes:
    0 — pipeline completed; report written
    1 — pipeline error (storage_state missing, network fail, etc)
    2 — pipeline completed BUT had >=1 🔴 issue findings (CI-friendly)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from browser_use.qa.pipeline import QAPipeline

logger = logging.getLogger('browser_use.qa.cli')


def _parse_paths(value: str | None) -> list[str]:
	if not value:
		return []
	return [p.strip() for p in value.split(',') if p.strip()]


def _parse_cookies(value: str | None) -> dict[str, str]:
	if not value:
		return {}
	out: dict[str, str] = {}
	for chunk in value.split(';'):
		if '=' not in chunk:
			continue
		name, _, val = chunk.partition('=')
		name = name.strip()
		if name:
			out[name] = val.strip()
	return out


def _build_parser() -> argparse.ArgumentParser:
	p = argparse.ArgumentParser(prog='qa-audit', description='Run the browser_use.qa pipeline against a URL.')
	p.add_argument('url', help="Target site, e.g. 'https://app.acme.com' or 'https://app.acme.com/dashboard'")
	p.add_argument('--auth', dest='auth_path', help='storage_state.json path (default: tmp/qa-auth.json)')
	p.add_argument('--out', dest='out_dir', help='Output directory for artifacts (default: qa-out)')
	p.add_argument('--paths', help='Comma-separated paths to seed the deep scan (default: auto-discover via sitemap)')
	p.add_argument('--label', dest='target_label', help='Subtitle label shown in the report')
	# Auth bootstrap modes — pick one:
	p.add_argument('--cookies', help='Cookie string "name=val;name=val" — writes storage_state then runs (mode: from_cookies)')
	p.add_argument('--bearer', help='Bearer token — stashed in localStorage under common SPA keys (mode: from_bearer)')
	p.add_argument(
		'--bearer-key',
		action='append',
		dest='bearer_keys',
		help='localStorage key for --bearer (repeatable; default: 4 common keys)',
	)
	p.add_argument('--form-email', help='Value to type into --form-email-selector (default selector: #email)')
	p.add_argument('--form-password', help='Value to type into --form-password-selector (default selector: #password)')
	p.add_argument('--form-email-selector', default='#email')
	p.add_argument('--form-password-selector', default='#password')
	p.add_argument('--form-submit-selector', default='button[type=submit]')
	p.add_argument('--form-login-url', help='Override the form login URL (default: <url>/login)')
	p.add_argument('--form-headful', action='store_true', help='Run the form-login pass headful for debugging')
	# Pipeline knobs:
	p.add_argument('--max-pages', type=int, default=30, help='Sitemap BFS page cap (default: 30)')
	p.add_argument('--max-depth', type=int, default=2, help='Sitemap BFS depth cap (default: 2)')
	p.add_argument('--hydration-sec', type=float, default=5.0, help='Post-navigate wait per page (default: 5.0)')
	p.add_argument('-v', '--verbose', action='store_true', help='Verbose logging')
	return p


async def _run(args: argparse.Namespace) -> int:
	logging.basicConfig(
		level=logging.DEBUG if args.verbose else logging.INFO,
		format='%(asctime)s %(levelname)-7s [%(name)s] %(message)s',
	)
	target_paths = _parse_paths(args.paths)

	auth_modes = sum(bool(x) for x in (args.cookies, args.bearer, args.form_email))
	if auth_modes > 1:
		logger.error('pick at most one of --cookies / --bearer / --form-email')
		return 1

	if args.cookies:
		pipeline = QAPipeline.from_cookies(
			args.url,
			_parse_cookies(args.cookies),
			auth_path=args.auth_path,
			report_dir=args.out_dir,
			target_paths=target_paths or None,
			target_label=args.target_label,
		)
	elif args.bearer:
		pipeline = QAPipeline.from_bearer(
			args.url,
			args.bearer,
			storage_keys=args.bearer_keys,
			auth_path=args.auth_path,
			report_dir=args.out_dir,
			target_paths=target_paths or None,
			target_label=args.target_label,
		)
	elif args.form_email:
		if not args.form_password:
			logger.error('--form-email also requires --form-password')
			return 1
		pipeline = await QAPipeline.from_form_login(
			args.url,
			login_url=args.form_login_url,
			fields={
				args.form_email_selector: args.form_email,
				args.form_password_selector: args.form_password,
			},
			submit_selector=args.form_submit_selector,
			headless=not args.form_headful,
			auth_path=args.auth_path,
			report_dir=args.out_dir,
			target_paths=target_paths or None,
			target_label=args.target_label,
		)
	else:
		pipeline = QAPipeline.from_url(
			args.url,
			auth_path=args.auth_path,
			report_dir=args.out_dir,
			target_paths=target_paths or None,
			target_label=args.target_label,
		)

	pipeline.max_crawl_pages = args.max_pages
	pipeline.max_crawl_depth = args.max_depth
	pipeline.hydration_sec = args.hydration_sec

	if not pipeline.storage_state_path.exists():
		logger.error(
			'storage_state missing at %s — capture it first via AuthCaptureSession '
			'or use one of --cookies / --bearer / --form-email',
			pipeline.storage_state_path,
		)
		return 1

	outputs = await pipeline.run()
	for name, path in outputs.items():
		logger.info('  %-22s -> %s', name, path)

	findings = json.loads(outputs['audit-findings'].read_text())
	by_sev = {'issue': 0, 'warn': 0, 'info': 0}
	for f in findings:
		by_sev[f['sev']] = by_sev.get(f['sev'], 0) + 1
	logger.info('rollup: %s', by_sev)
	# Fail the run for CI when any 🔴 issues were found
	return 2 if by_sev.get('issue', 0) > 0 else 0


def cli() -> None:
	"""Entry point used by the `qa-audit` console script."""
	args = _build_parser().parse_args()
	sys.exit(asyncio.run(_run(args)))


if __name__ == '__main__':
	cli()
