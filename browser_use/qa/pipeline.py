"""
QAPipeline — orchestrator stub for the QA audit application layer.

W1 ships the parameterized class shape so downstream W2 can fill in
the actual scan/probe/audit/report calls without churning the public
interface.

Usage (target shape, fully wired in W2):

    from pathlib import Path
    from browser_use.qa import QAPipeline

    pipeline = QAPipeline(
        base_url="http://localhost:5234",
        storage_state_path=Path("tmp/onplane.storage.json"),
        target_paths=["/dashboard", "/users", "/billing"],
        report_dir=Path("tmp/qa-out"),
    )
    await pipeline.run()
    # → writes report_dir/qa-pages.json, link-probe.json,
    #   spa-route-probe.json, audit-findings.json, qa-report.md,
    #   qa-report.json

Public surface (locked here so W2 swaps in real implementations
without breaking call sites):

  QAPipeline(base_url, storage_state_path, target_paths, report_dir,
             hydration_sec=5, mobile_viewport=(375, 812),
             max_crawl_pages=30, max_crawl_depth=2)
    .run()                  -> dict[str, Path]  # output_name → path
    .scan_pages()           -> list[dict]
    .crawl_sitemap()        -> dict
    .probe_links()          -> list[dict]
    .probe_spa_routes()     -> list[dict]
    .audit()                -> list[dict]
    .render_report()        -> tuple[Path, Path]  # (markdown, json)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class QAPipeline:
	"""Run a 12-category QA audit against an authenticated SPA.

	W1 scaffold: stores params and exposes the public method set.
	Each method body is intentionally unimplemented (stub-ok: W1 scaffold).
	W2 promotes the real logic from `examples/qa_audit/`.
	"""

	base_url: str
	"""Origin to audit, e.g. 'http://localhost:5234'. Used as the prefix for
	relative `target_paths` and as the same-origin boundary for the crawler."""

	storage_state_path: Path
	"""Playwright-format storage_state JSON written by
	`browser_use.integrations.webmap.AuthCaptureSession`. Required — the
	pipeline does not capture login state itself."""

	target_paths: list[str] = field(default_factory=list)
	"""Path strings (e.g. '/dashboard') to seed the deep scan + crawl with."""

	report_dir: Path = field(default_factory=lambda: Path('qa-out'))
	"""Where all JSON + Markdown outputs land. Created if missing."""

	hydration_sec: int = 5
	"""Seconds to wait after `_cdp_navigate` before scanning. SPAs with heavy
	post-mount fetches need more; static-ish dashboards work at 3-4."""

	mobile_viewport: tuple[int, int] = (375, 812)
	"""(width, height) for the mobile-friendliness pass. iPhone 14 by default."""

	max_crawl_pages: int = 30
	"""Hard cap on the sitemap BFS so the pipeline terminates on huge apps."""

	max_crawl_depth: int = 2
	"""BFS depth cap. depth=0 is the seed list, depth=1 adds links found on
	seeds, depth=2 adds links from those. Most real apps surface 80% of routes
	by depth=2."""

	async def run(self) -> dict[str, Path]:
		"""Run the full pipeline end-to-end. Returns a map of artifact
		name → on-disk path."""
		raise NotImplementedError('W2 wires this — see issue #2')  # stub-ok: W1 scaffold per owner milestone DAG, logic ships in W2

	async def scan_pages(self) -> list[dict]:
		"""Deep-scan each target_path via webmap + AX + CDP listeners."""
		raise NotImplementedError('W2 wires this — see issue #2')  # stub-ok: W1 scaffold per owner milestone DAG, logic ships in W2

	async def crawl_sitemap(self) -> dict:
		"""BFS-crawl same-origin links from the target seeds. Captures
		URLs, route templates, and XHR/fetch API inventory."""
		raise NotImplementedError('W2 wires this — see issue #2')  # stub-ok: W1 scaffold per owner milestone DAG, logic ships in W2

	async def probe_links(self) -> list[dict]:
		"""HEAD-probe every unique href found across all scanned pages."""
		raise NotImplementedError('W2 wires this — see issue #2')  # stub-ok: W1 scaffold per owner milestone DAG, logic ships in W2

	async def probe_spa_routes(self) -> list[dict]:
		"""Navigate each same-origin link in an authenticated browser to
		catch SPA-level 404s and error boundaries that HEAD probes miss."""
		raise NotImplementedError('W2 wires this — see issue #2')  # stub-ok: W1 scaffold per owner milestone DAG, logic ships in W2

	def audit(self, scans: list[dict], links: list[dict], spa: list[dict]) -> list[dict]:
		"""Apply the 12 audit categories against the captured artifacts."""
		raise NotImplementedError('W2 wires this — see issue #2')  # stub-ok: W1 scaffold per owner milestone DAG, logic ships in W2

	def render_report(self, scans: list[dict], links: list[dict], spa: list[dict], findings: list[dict]) -> tuple[Path, Path]:
		"""Write the Markdown + JSON report. Returns (md_path, json_path)."""
		raise NotImplementedError('W2 wires this — see issue #2')  # stub-ok: W1 scaffold per owner milestone DAG, logic ships in W2
