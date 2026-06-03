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

W1 — package scaffold. Logic in `pipeline.py` lands in W2 alongside
`categories.py`, `sitemap.py`, `report.py`, and `_probes.py`. See
https://github.com/bentheautomator/browser-use/issues/2 for the milestone DAG.
"""

from browser_use.qa.pipeline import QAPipeline

__all__ = ['QAPipeline']
