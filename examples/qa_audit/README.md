# QA Audit Pipeline

End-to-end automated QA against an authenticated SPA, built on the
webmap integration's `AuthCaptureSession` + `AuthScanSession` +
`WebmapExtractor`.

**Status:** W2 promoted the logic into `browser_use.qa` — the
`run_pipeline.py` example below is the canonical entry point. The other
scripts are kept as low-level demos showing each pipeline stage in
isolation, but they call the same `browser_use.qa.*` modules under the
hood.

## Quick start

```bash
# 1. one-time: capture login (headful, sentinel-file driven)
uv run python examples/qa_audit/capture_login.py
# log in via the browser, then in another shell:
touch tmp/login-done

# 2. install the [qa] extra so httpx is available for the link probe
uv sync --extra qa

# 3. run the full pipeline (single call, all artifacts)
uv run python examples/qa_audit/run_pipeline.py
# → writes tmp/qa-out/{qa-pages, sitemap-*, link-probe, spa-route-probe,
#   audit-findings, qa-report}.json + qa-report.md
```

## Single call

```python
from pathlib import Path
from browser_use.qa import QAPipeline

await QAPipeline(
    base_url='http://localhost:5234',
    storage_state_path=Path('tmp/onplane.storage.json'),
    target_paths=['/dashboard', '/users', '/billing'],
    report_dir=Path('tmp/qa-out'),
).run()
```

## Stack

```
capture_login.py    — headful chromium, sentinel-file driven, persists storage_state
scan_authed.py      — single-page sanity scan with restored auth
scan_batch.py       — batch scan of N pages with restored auth
qa_scan.py          — deep per-page scan (webmap + AX + console + network +
                      mobile viewport + heading hierarchy + DOM link enum)
link_probe.py       — httpx HEAD probe of all unique hrefs
spa_route_probe.py  — second AuthScanSession pass: navigate each same-origin
                      link, detect SPA-level 404 + error-boundary text
sitemap.py          — BFS URL + route + API discovery
audit.py            — 8-category audit (proposed UX heuristics)
audit_full.py       — 12-category audit (proposed UX + Nielsen 10 + WCAG 2.2 AA)
report.py           — Markdown + JSON combined report renderer
run_pipeline.py     — single-call orchestration via browser_use.qa.QAPipeline
```

## Audit categories

1. Landmarks & Structure        (WCAG 1.3.1)
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
11. Mobile Friendliness         (viewport meta + 375px overflow check)
12. Heading Order & Page Titles (h1 count, level jumps, title uniqueness)
