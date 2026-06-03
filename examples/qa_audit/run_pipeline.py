"""End-to-end QA audit via the W2 sub-package — single call, all artifacts.

Replaces the W1 multi-script pipeline (qa_scan.py + link_probe.py +
spa_route_probe.py + audit_full.py + report.py) with one orchestrator:

    QAPipeline(...).run()
        → tmp/qa-out/qa-pages.json
        → tmp/qa-out/sitemap-pages.json
        → tmp/qa-out/sitemap-routes.json
        → tmp/qa-out/sitemap-apis.json
        → tmp/qa-out/link-probe.json
        → tmp/qa-out/spa-route-probe.json
        → tmp/qa-out/audit-findings.json
        → tmp/qa-out/qa-report.md
        → tmp/qa-out/qa-report.json

Prerequisites:
    1. Run examples/qa_audit/capture_login.py once to write
       tmp/onplane.storage.json (the authenticated storage_state).
    2. Have the target app running at BASE_URL.
    3. pip install "browser-use[qa]" (or uv sync) so httpx is available
       for the HTTP link-probe stage.

Run:
    uv run python examples/qa_audit/run_pipeline.py
"""

import asyncio
from pathlib import Path

from browser_use.qa import QAPipeline

REPO = Path(__file__).resolve().parents[2]
STORAGE = REPO / 'tmp' / 'onplane.storage.json'
REPORT_DIR = REPO / 'tmp' / 'qa-out'
BASE_URL = 'http://localhost:5234'
TARGET_PATHS = [
	'/t/on-plane-consulting/client-metrics',
	'/t/on-plane-consulting/sales-pipeline',
	'/t/on-plane-consulting/capacity',
]


async def main() -> None:
	pipeline = QAPipeline(
		base_url=BASE_URL,
		storage_state_path=STORAGE,
		target_paths=TARGET_PATHS,
		report_dir=REPORT_DIR,
		target_label='FCO Command Center @ localhost:5234 (on-plane-consulting)',
	)
	outputs = await pipeline.run()
	print('\n=== pipeline outputs ===')
	for name, path in outputs.items():
		size = path.stat().st_size if path.exists() else 0
		print(f'  {name:20s} -> {path} ({size} bytes)')


if __name__ == '__main__':
	asyncio.run(main())
