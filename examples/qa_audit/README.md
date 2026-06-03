# QA Audit Pipeline

End-to-end automated QA against an authenticated SPA, built on the webmap integration's `AuthCaptureSession` + `AuthScanSession` + `WebmapExtractor`.

## Stack

```
capture_login.py    → headful chromium, sentinel-file driven, persists storage_state
scan_authed.py      → single-page sanity scan with restored auth
scan_batch.py       → batch scan of N pages with restored auth
qa_scan.py          → deep per-page scan: webmap + AX + console + network +
                      mobile viewport + heading hierarchy + DOM link enum
link_probe.py       → httpx HEAD probe (cookies restored) of all unique hrefs
spa_route_probe.py  → second AuthScanSession pass: navigate each same-origin
                      link, detect SPA-level 404 + error-boundary text
audit.py            → 8-category audit (proposed UX heuristics)
audit_full.py       → 12-category audit (proposed UX + Nielsen 10 + WCAG 2.2 AA)
report.py           → beautiful Markdown + JSON combined report
```

## Run order

```bash
# 1. one-time: capture login state (headful, sentinel-file driven)
uv run python examples/qa_audit/capture_login.py
#    log in via the browser, then in another shell:
touch tmp/login-done

# 2. deep scan each target page
uv run python examples/qa_audit/qa_scan.py

# 3. HTTP probe every unique href
uv run python examples/qa_audit/link_probe.py

# 4. SPA-render probe every same-origin link
uv run python examples/qa_audit/spa_route_probe.py

# 5. apply the 12-category audit
uv run python examples/qa_audit/audit_full.py

# 6. render the report
uv run python examples/qa_audit/report.py
# → tmp/qa-report.md, tmp/qa-report.json
```

## Categories

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

## Hard-coded knobs

The scripts target `http://localhost:5234` and a fixed page set for now —
edit the `BASE`, `PAGES`, `STORAGE` constants per use.
