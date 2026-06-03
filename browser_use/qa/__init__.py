"""
QA — Automated audit pipeline on top of browser-use + webmap.

This sub-package is the application layer that the webmap integration
(`browser_use.integrations.webmap`) sits underneath. It composes
`WebmapExtractor`, `AuthCaptureSession`, `AuthScanSession`, and direct
CDP listeners into a pipeline that:

  - scans N URLs of an authenticated SPA
  - captures console errors, JS exceptions, network failures, and 4xx/5xx
    sub-resource responses during render
  - crawls the same-origin link graph BFS
  - HEAD-probes every unique href
  - rerun-probes each same-origin route via a real browser to detect
    client-side 404s and error boundaries
  - applies a 12-category audit (UX heuristics + Nielsen 10 + WCAG 2.2 AA)
  - renders a beautiful Markdown + JSON report

W2 promoted the logic from `examples/qa_audit/` into this package. See
the `examples/qa_audit/README.md` for a usage walkthrough and example
scripts that drive the pipeline end-to-end.
"""

from browser_use.qa.categories import CATEGORIES, run_audit
from browser_use.qa.pipeline import QAPipeline
from browser_use.qa.report import render_combined_json, render_markdown
from browser_use.qa.scanner import scan_page, scan_pages
from browser_use.qa.sitemap import crawl_sitemap, route_template_from_url

__all__ = [
	'CATEGORIES',
	'QAPipeline',
	'crawl_sitemap',
	'render_combined_json',
	'render_markdown',
	'route_template_from_url',
	'run_audit',
	'scan_page',
	'scan_pages',
]
