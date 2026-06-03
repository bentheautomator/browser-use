"""
QAPipeline — orchestrator for the browser_use.qa automated audit.

Composes:
  - browser_use.qa.scanner.scan_pages        (per-URL deep capture)
  - browser_use.qa.sitemap.crawl_sitemap     (BFS URL + route + API discovery)
  - browser_use.qa._probes.probe_links_http  (HEAD probe of every href)
  - browser_use.qa._probes.probe_spa_routes  (SPA-level 404 + error-boundary detection)
  - browser_use.qa.categories.run_audit      (12-category audit)
  - browser_use.qa.report.render_markdown    (beautiful Markdown report)
  - browser_use.qa.report.render_combined_json (machine-readable bundle)

Auth is the caller's responsibility — pass an existing
`storage_state_path` written by
`browser_use.integrations.webmap.AuthCaptureSession`. The pipeline opens
an `AuthScanSession` against it, runs everything, then writes nine
artifacts to `report_dir`:

    qa-pages.json        — per-page deep scan output
    sitemap-pages.json   — sitemap crawler raw pages
    sitemap-routes.json  — route templates
    sitemap-apis.json    — API endpoint inventory
    link-probe.json      — HTTP HEAD probe of every href
    spa-route-probe.json — SPA render probe of every same-origin route
    audit-findings.json  — flat list of category findings
    qa-report.md         — beautiful Markdown report
    qa-report.json       — combined JSON bundle

Usage:
    from pathlib import Path
    from browser_use.qa import QAPipeline

    pipeline = QAPipeline(
        base_url='http://localhost:5234',
        storage_state_path=Path('tmp/onplane.storage.json'),
        target_paths=['/dashboard', '/users', '/billing'],
        report_dir=Path('tmp/qa-out'),
    )
    await pipeline.run()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from browser_use.integrations.webmap import AuthScanSession
from browser_use.qa import _probes, categories, report, sitemap
from browser_use.qa.scanner import scan_pages as scan_pages_impl

if TYPE_CHECKING:
	from browser_use.browser.session import BrowserSession

logger = logging.getLogger(__name__)


@dataclass
class QAPipeline:
	"""Run a 12-category QA audit against an authenticated SPA."""

	base_url: str
	"""Origin to audit, e.g. 'http://localhost:5234'."""

	storage_state_path: Path
	"""Playwright-format storage_state JSON path. Required."""

	target_paths: list[str] = field(default_factory=list)
	"""Path strings (e.g. '/dashboard') to seed the deep scan + crawl with."""

	report_dir: Path = field(default_factory=lambda: Path('qa-out'))
	"""Where all JSON + Markdown outputs land. Created if missing."""

	hydration_sec: float = 5.0
	"""Seconds to wait after `_cdp_navigate` before scanning."""

	mobile_viewport: tuple[int, int] = (375, 812)
	"""(width, height) for the mobile-friendliness pass."""

	max_crawl_pages: int = 30
	"""Hard cap on the sitemap BFS so the pipeline terminates on huge apps."""

	max_crawl_depth: int = 2
	"""BFS depth cap."""

	target_label: str | None = None
	"""Optional short label shown in the report subtitle."""

	def _seed_urls(self) -> list[str]:
		return [self.base_url + p if not p.startswith('http') else p for p in self.target_paths]

	def _write_json(self, name: str, payload: object) -> Path:
		self.report_dir.mkdir(parents=True, exist_ok=True)
		path = self.report_dir / name
		path.write_text(json.dumps(payload, indent=2))
		return path

	async def scan_pages(self, session: BrowserSession) -> list[dict]:
		"""Deep-scan each `target_paths` URL via the live session."""
		return await scan_pages_impl(
			session,
			self._seed_urls(),
			base_url=self.base_url,
			hydration_sec=self.hydration_sec,
			mobile_viewport=self.mobile_viewport,
		)

	async def crawl_sitemap(self, session: BrowserSession) -> dict:
		"""BFS-crawl from the seed URLs."""
		return await sitemap.crawl_sitemap(
			session,
			self._seed_urls(),
			base_url=self.base_url,
			max_depth=self.max_crawl_depth,
			max_pages=self.max_crawl_pages,
		)

	async def probe_links(self, scans: list[dict]) -> list[dict]:
		"""HEAD-probe every unique href found across all scanned pages."""
		hrefs: set[str] = set()
		for page in scans:
			for link in page.get('all_links', []):
				h = (link.get('href') or '').strip()
				if h and not h.startswith(('javascript:', 'mailto:')):
					hrefs.add(h)
		cookies = self._load_cookies()
		return await _probes.probe_links_http(sorted(hrefs), cookies=cookies)

	async def probe_spa_routes(self, session: BrowserSession, scans: list[dict]) -> list[dict]:
		"""Navigate each unique same-origin link via the authenticated session."""
		hrefs: set[str] = set()
		base_netloc = urlparse(self.base_url).netloc
		for page in scans:
			for link in page.get('all_links', []):
				h = (link.get('href') or '').strip().split('#')[0]
				if not h or h.startswith(('javascript:', 'mailto:')):
					continue
				p = urlparse(h)
				if p.scheme not in ('http', 'https'):
					continue
				if p.netloc and p.netloc != base_netloc:
					continue
				hrefs.add(h)
		return await _probes.probe_spa_routes(session, sorted(hrefs))

	def audit(self, scans: list[dict], link_probe: list[dict], spa_probe: list[dict]) -> list[dict]:
		"""Apply the 12 audit categories."""
		return categories.run_audit(scans, link_probe, spa_probe)

	def render_report(
		self,
		scans: list[dict],
		link_probe: list[dict],
		spa_probe: list[dict],
		findings: list[dict],
	) -> tuple[Path, Path]:
		"""Write Markdown + combined JSON. Returns (md_path, json_path)."""
		md = report.render_markdown(
			scans,
			link_probe,
			spa_probe,
			findings,
			target_label=self.target_label,
			base_url=self.base_url,
		)
		combined = report.render_combined_json(
			scans,
			link_probe,
			spa_probe,
			findings,
			target_label=self.target_label,
		)
		self.report_dir.mkdir(parents=True, exist_ok=True)
		md_path = self.report_dir / 'qa-report.md'
		md_path.write_text(md)
		json_path = self._write_json('qa-report.json', combined)
		return md_path, json_path

	def _load_cookies(self) -> dict[str, str]:
		try:
			data = json.loads(self.storage_state_path.read_text())
		except Exception:
			return {}
		return {c['name']: c['value'] for c in data.get('cookies', []) if 'name' in c and 'value' in c}

	async def run(self) -> dict[str, Path]:
		"""Run the full pipeline end-to-end inside one authenticated session."""
		outputs: dict[str, Path] = {}
		async with AuthScanSession(self.storage_state_path, headless=True) as session:
			logger.info('🔎 deep scan of seed pages')
			scans = await self.scan_pages(session)
			outputs['qa-pages'] = self._write_json('qa-pages.json', scans)

			logger.info('🗺️  sitemap crawl')
			smap = await self.crawl_sitemap(session)
			outputs['sitemap-pages'] = self._write_json('sitemap-pages.json', smap.get('pages', []))
			outputs['sitemap-routes'] = self._write_json('sitemap-routes.json', smap.get('route_templates', []))
			outputs['sitemap-apis'] = self._write_json('sitemap-apis.json', smap.get('api_endpoints', []))

			logger.info('🛰️  SPA route probe')
			spa_probe = await self.probe_spa_routes(session, scans)
			outputs['spa-route-probe'] = self._write_json('spa-route-probe.json', spa_probe)

		logger.info('🔗 HTTP link probe (cookies restored)')
		link_probe = await self.probe_links(scans)
		outputs['link-probe'] = self._write_json('link-probe.json', link_probe)

		logger.info('🧪 12-category audit')
		findings = self.audit(scans, link_probe, spa_probe)
		outputs['audit-findings'] = self._write_json('audit-findings.json', findings)

		logger.info('📝 render report')
		md_path, json_path = self.render_report(scans, link_probe, spa_probe, findings)
		outputs['qa-report-md'] = md_path
		outputs['qa-report-json'] = json_path
		return outputs
