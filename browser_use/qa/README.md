# browser_use.qa — Automated QA Audit Pipeline

End-to-end automated QA against an authenticated SPA. Point it at a URL, get back a beautiful Markdown report plus machine-readable JSON covering 12 categories of UX/a11y/runtime/route findings. Built on top of `browser_use.integrations.webmap` + Chrome DevTools Protocol.

```bash
qa-audit https://app.acme.com --cookies 'session=eyJ...'
# → qa-out/qa-report.md + qa-report.json + 7 raw artifacts
```

---

## Why

You can run a manual QA pass: log in, click around, watch the console, check mobile, file bugs. That doesn't scale to 30 routes × every commit. `browser_use.qa` does the click-around for you:

- BFS-crawls the app from a seed URL to discover every reachable route
- Visits each route in an authenticated headless browser
- Captures console errors, JS exceptions, failed network requests, HTTP statuses
- Detects SPA-level 404s (HTTP 200 + in-DOM "Page Not Found") that uptime monitors miss
- Renders each page at mobile width to catch overflow + missing viewport meta
- Runs a 12-category audit synthesized from Nielsen 10 + WCAG 2.2 AA + practical UX heuristics
- Probes every link with HEAD requests
- Renders a Markdown report and dumps everything to JSON for CI/diff workflows

Result: 1 command, 9 artifacts, exit code 2 if any 🔴 issues found.

---

## Install

```bash
# with the qa extra so httpx is available for the link probe
pip install 'browser-use[qa]'
# or
uv add 'browser-use[qa]'
```

One-time setup for the webmap CLI it depends on:

```bash
# in the webmap repo:
pnpm exec playwright install chromium
```

That installs the pinned Chromium build the webmap scanner targets.

---

## Quickstart — one liner

The CLI is the fastest path:

```bash
# Discovers all reachable routes from acme.com root and audits each
qa-audit https://app.acme.com --cookies 'session=eyJhbGc...'
```

Outputs land in `./qa-out/`. Exit code is 2 if any 🔴 issues found — perfect for `set -e` CI scripts.

---

## Auth modes

Pick the one that matches your app. All four end up in the same place — a Playwright-format `storage_state.json` that the headless audit session restores.

### A. Storage-state file (any auth flow — magic-link, OAuth, 2FA, SSO)

Capture once via a real browser, reuse forever (or until the cookies expire). Best for weird login flows you can't script.

```python
import asyncio
from pathlib import Path
from browser_use.integrations.webmap import AuthCaptureSession
from browser_use.qa import QAPipeline

async def setup():
    # Headful chromium opens; you log in by hand, press ENTER in the terminal
    async with AuthCaptureSession(Path('tmp/auth.json')) as s:
        await s._cdp_navigate('https://app.acme.com/login')
        input('Log in via the browser, press ENTER when done: ')

async def audit():
    await QAPipeline.from_url(
        'https://app.acme.com',
        auth_path='tmp/auth.json',
    ).run()

asyncio.run(setup())
asyncio.run(audit())
```

Or via the CLI (storage already exists):

```bash
qa-audit https://app.acme.com --auth tmp/auth.json
```

### B. Direct cookies (CI / service accounts)

You already have a session token from elsewhere (CI secret, dev console, service-account endpoint). Skip the UI login entirely:

```python
await QAPipeline.from_cookies(
    'https://app.acme.com',
    cookies={'session': 'eyJhbGc...', 'csrf': 'xyz...'},
).run()
```

```bash
qa-audit https://app.acme.com --cookies 'session=eyJhbGc...;csrf=xyz...'
```

Extra cookie attributes are available as kwargs (`domain`, `secure`, `http_only`, `same_site`). Sensible defaults derive `domain` from the URL.

### C. Bearer token (SPAs that read from localStorage)

Most React/Vue/Angular apps stash their auth token under a `localStorage` key like `auth_token`, `accessToken`, `id_token`, or `jwt`. `from_bearer` writes to all four common keys by default:

```python
await QAPipeline.from_bearer(
    'https://app.acme.com',
    bearer='eyJhbGciOiJIUzI1NiIs...',
).run()
```

```bash
qa-audit https://app.acme.com --bearer eyJhbGc...
```

Tell it which key your SPA actually reads:

```python
await QAPipeline.from_bearer(
    'https://app.acme.com',
    bearer='eyJ...',
    storage_keys=['my_company_token'],
).run()
```

```bash
qa-audit https://app.acme.com --bearer eyJ... --bearer-key my_company_token
```

If your app sends the bearer in an `Authorization` HTTP header on every fetch (instead of reading from localStorage) — use the storage-state file flow (A) and capture the session via a real browser instead. That captures whatever interception the app does at runtime.

### D. Form-fill autologin (email + password)

Scripts a traditional login form for you. Works whenever you can describe the form via CSS selectors:

```python
import os

await QAPipeline.from_form_login(
    'https://app.acme.com',
    fields={
        '#email': 'qa@acme.com',
        '#password': os.environ['QA_PASSWORD'],
    },
    submit_selector='button[type=submit]',
).run()
```

```bash
qa-audit https://app.acme.com \
    --form-email qa@acme.com \
    --form-password "$QA_PASSWORD" \
    --form-email-selector '#email' \
    --form-password-selector '#password'
```

Defaults: `login_url=<base>/login`, `email_selector=#email`, `password_selector=#password`, `submit_selector=button[type=submit]`, `wait_after_submit_sec=4.0`, `headless=True`. Pass `--form-headful` to watch the script run when debugging.

---

## Dynamic discovery (no hand-maintained path list)

When you don't pass `target_paths`, `QAPipeline.run()` crawls the site BFS from the base URL first and uses every discovered same-origin URL as a deep-scan seed:

```python
await QAPipeline.from_url('https://app.acme.com').run()
# crawls / → discovers /dashboard, /users, /billing, /admin, /help, ...
# audits each one
```

```bash
# same thing via CLI:
qa-audit https://app.acme.com
```

Tune the crawl:

```python
QAPipeline.from_url(
    'https://app.acme.com',
    target_paths=[],          # empty → auto-discover
).run()

# or explicit:
QAPipeline.from_url(
    'https://app.acme.com',
    target_paths=['/dashboard', '/users', '/billing'],
).run()
```

Crawl knobs (CLI flags or constructor kwargs):

| Flag | Default | Meaning |
|---|---|---|
| `--max-pages N` | 30 | Hard cap on BFS visits |
| `--max-depth N` | 2 | BFS depth ceiling |
| `--hydration-sec N` | 5.0 | Per-page post-navigate wait |

When `target_paths` is non-empty, the sitemap crawl still runs alongside the deep scan — you get full route + API inventory for the rest of the surface even though only the listed paths are deep-scanned.

---

## CLI reference

```bash
qa-audit URL [options]
```

| Flag | Description |
|---|---|
| `URL` | Target site, e.g. `https://app.acme.com` or `https://app.acme.com/dashboard` |
| `--auth PATH` | storage_state.json path (default: `tmp/qa-auth.json`) |
| `--out DIR` | Output dir (default: `qa-out`) |
| `--paths LIST` | Comma-separated paths to deep-scan (default: auto-discover) |
| `--label STR` | Subtitle on the report |
| `--cookies STR` | Cookie pairs `name=val;name=val` — uses `from_cookies` |
| `--bearer STR` | Bearer token — uses `from_bearer` |
| `--bearer-key KEY` | localStorage key for bearer (repeatable) |
| `--form-email STR` | Email value — uses `from_form_login` |
| `--form-password STR` | Password value (require with `--form-email`) |
| `--form-email-selector CSS` | (default: `#email`) |
| `--form-password-selector CSS` | (default: `#password`) |
| `--form-submit-selector CSS` | (default: `button[type=submit]`) |
| `--form-login-url URL` | Override default `<url>/login` |
| `--form-headful` | Watch the form-fill flow run |
| `--max-pages N` | Sitemap BFS cap (default: 30) |
| `--max-depth N` | Sitemap BFS depth (default: 2) |
| `--hydration-sec N` | Per-page wait (default: 5.0) |
| `-v / --verbose` | Verbose logging |

**Exit codes:**

- `0` — pipeline ran clean, 0 🔴 issues
- `1` — pipeline error (storage missing, network failure, etc)
- `2` — pipeline ran clean BUT >=1 🔴 issue findings (CI-friendly — fail the build)

---

## Output artifacts

Every run writes 9 files to `--out` (default `qa-out/`):

| File | Contents |
|---|---|
| `qa-report.md` | Human-readable Markdown report — read this first |
| `qa-report.json` | Machine-readable bundle pairing with the Markdown |
| `qa-pages.json` | Raw per-page deep scan (webmap + AX + CDP captures) |
| `sitemap-pages.json` | URLs visited by the BFS crawler |
| `sitemap-routes.json` | Clustered route templates (e.g. `/users/[id]`) |
| `sitemap-apis.json` | XHR/fetch endpoint inventory with statuses |
| `link-probe.json` | HTTP HEAD probe of every discovered href |
| `spa-route-probe.json` | SPA-render probe of every same-origin route |
| `audit-findings.json` | Flat list of `{cat, sev, msg, url}` |

---

## 12 audit categories

Synthesized from Nielsen's 10 heuristics + WCAG 2.2 AA + practical UX rules:

| # | Category | Standard |
|---|---|---|
| 1 | Landmarks & Structure | WCAG 1.3.1, Nielsen consistency |
| 2 | Navigation Clarity | WCAG 2.4, Nielsen recognition > recall |
| 3 | Actions & Affordances | Nielsen match real-world |
| 4 | Forms & Inputs | WCAG 3.3.2, Nielsen error prevention |
| 5 | A11y Name Coverage | WCAG 4.1.2 Name/Role/Value |
| 6 | Label Hygiene | WCAG 2.4.6, Nielsen consistency |
| 7 | Interactive Density | Nielsen aesthetic/minimalist |
| 8 | Auth Posture / System Status | WCAG 4.1.3, Nielsen visibility |
| 9 | Routes & Network Health | HTTP status of links + main doc |
| 10 | Runtime Errors & Broken Data | Console, JS exceptions, SPA-404 |
| 11 | Mobile Friendliness | Viewport meta + 375px overflow |
| 12 | Heading Order & Page Titles | h1 count, level jumps, title uniqueness |

Each finding has severity `issue` 🔴 / `warn` 🟡 / `info` 🟢. `issue` findings fail the CI exit code (2). `warn` findings are listed but don't fail the build by default. `info` is informational counts.

Add or override categories: `CATEGORIES` registry is exported. See `browser_use/qa/categories.py` for the canonical 12.

---

## CI integration

GitHub Actions example:

```yaml
- name: QA audit
  env:
    QA_SESSION: ${{ secrets.QA_SESSION_COOKIE }}
  run: |
    pip install 'browser-use[qa]'
    qa-audit https://staging.acme.com \
        --cookies "session=$QA_SESSION" \
        --out qa-out

- name: Upload artifacts
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: qa-report
    path: qa-out/

- name: Fail on 🔴 issues
  run: |
    # qa-audit exits 2 if there are issue-severity findings;
    # the previous step already exited 0 if nothing was found.
    # Keep this step for explicit diff between warn-only and clean runs.
    test -f qa-out/audit-findings.json
```

File issues automatically:

```python
import json, subprocess
from pathlib import Path

findings = json.loads(Path('qa-out/audit-findings.json').read_text())
for f in [x for x in findings if x['sev'] == 'issue']:
    subprocess.run([
        'gh', 'issue', 'create',
        '--repo', 'acme/app',
        '--label', 'bug',
        '--title', f'qa: [Cat {f["cat"]}] {f["msg"][:60]}',
        '--body', f'Auto-filed by qa-audit\n\nURL: {f["url"]}\nCategory: {f["category"]}\n\n{f["msg"]}',
    ])
```

---

## Customization

Skip the orchestrator, drive individual stages:

```python
from browser_use.qa import (
    crawl_sitemap, scan_pages, run_audit,
    render_markdown, render_combined_json,
)
from browser_use.qa._probes import probe_links_http, probe_spa_routes
from browser_use.integrations.webmap import AuthScanSession

async with AuthScanSession('tmp/auth.json') as session:
    pages = await scan_pages(session, ['https://x/dashboard'])
    smap  = await crawl_sitemap(session, ['https://x/'], max_pages=50)
    spa   = await probe_spa_routes(session, ['https://x/users'])

link = await probe_links_http(['https://x/users'])
findings = run_audit(pages, link, spa)
md = render_markdown(pages, link, spa, findings)
```

Add a custom audit category:

```python
from browser_use.qa.categories import CATEGORIES

def cat13_my_check(page):
    if page['interactive_count'] > 100:
        yield {'cat': 13, 'sev': 'warn', 'msg': f'page is too busy ({page["interactive_count"]} interactives)'}

CATEGORIES.append((13, 'My Custom Check', cat13_my_check, False, False))
```

Subclass `QAPipeline` to inject custom behavior (e.g. write outputs to S3 instead of disk):

```python
from browser_use.qa import QAPipeline

class S3QAPipeline(QAPipeline):
    def _write_json(self, name, payload):
        import boto3, json
        boto3.client('s3').put_object(
            Bucket='qa-reports',
            Key=f'{self.run_id}/{name}',
            Body=json.dumps(payload),
        )
        return Path(f's3://qa-reports/{self.run_id}/{name}')
```

---

## Examples

Worked examples live in [`examples/qa_audit/`](../../examples/qa_audit/):

- `run_pipeline.py` — single-call canonical entry point
- `capture_login.py` — headful sentinel-file login capture
- `scan_authed.py` — single-page sanity scan
- `scan_batch.py` — batch scan with restored auth
- `qa_scan.py` — deep per-page scan internals
- `link_probe.py` — link HEAD-probe internals
- `spa_route_probe.py` — SPA route probe internals
- `sitemap.py` — sitemap crawler internals
- `audit_full.py` — 12-category audit driver
- `report.py` — Markdown + JSON report renderer

---

## Public API

```python
from browser_use.qa import (
    QAPipeline,                  # main orchestrator
    CATEGORIES,                  # 12 audit categories registry
    run_audit,                   # apply all 12 categories to scan output
    scan_page,                   # deep scan one URL
    scan_pages,                  # deep scan N URLs
    crawl_sitemap,               # BFS crawl
    route_template_from_url,     # normalize URL → /t/[slug]/...
    render_markdown,             # render report
    render_combined_json,        # render JSON bundle
    write_storage_from_cookies,  # auth: cookies → storage_state
    write_storage_from_localstorage,  # auth: bearer → storage_state
    capture_via_form_login,      # auth: form-fill login
)
```

`QAPipeline` classmethods:

```python
QAPipeline.from_url(url, auth_path=..., target_paths=..., report_dir=...)
QAPipeline.from_cookies(base_url, cookies={...}, ...)
QAPipeline.from_bearer(base_url, bearer='eyJ...', storage_keys=[...], ...)
QAPipeline.from_form_login(base_url, fields={...}, submit_selector='...', ...)
```

`QAPipeline` instance methods:

```python
await pipeline.run()                                  # full pipeline
await pipeline.scan_pages(session)                    # just deep scan
await pipeline.crawl_sitemap(session)                 # just sitemap
await pipeline.probe_links(scans)                     # just HEAD probe
await pipeline.probe_spa_routes(session, scans)       # just SPA probe
pipeline.audit(scans, link_probe, spa_probe)          # just audit
pipeline.render_report(scans, link, spa, findings)    # just rendering
```

---

## Provenance

Built as the application layer on top of `browser_use.integrations.webmap`. Wave-based delivery — see issue #2 for the milestone DAG.

- W1 — package scaffold + `[qa]` extras
- W2 — promoted logic from `examples/qa_audit/` into the sub-package
- W3 — pytest coverage (21 deterministic tests + httpserver fixtures)
- W4 — dynamic discovery + 4 auth modes + `qa-audit` CLI (this README)
