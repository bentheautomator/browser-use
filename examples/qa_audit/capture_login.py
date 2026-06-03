"""Capture on-plane login state via sentinel-file signal.

Run:
    cd /Users/automator/git/bentheautomator/browser-use
    uv run python tmp/capture_login.py

Browser opens at LOGIN_URL once. Script then sits quietly until the
sentinel file tmp/login-done exists. Log in via the browser at your
own pace, then in any other shell:

    touch /Users/automator/git/bentheautomator/browser-use/tmp/login-done

Script wakes, saves state to tmp/onplane.storage.json, exits.

Abort early without saving: touch tmp/abort-capture
"""
import asyncio
import sys
from pathlib import Path

REPO = Path("/Users/automator/git/bentheautomator/browser-use")
sys.path.insert(0, str(REPO))

from browser_use.integrations.webmap import AuthCaptureSession

STORAGE = REPO / "tmp" / "onplane.storage.json"
DONE = REPO / "tmp" / "login-done"
ABORT = REPO / "tmp" / "abort-capture"
LOGIN_URL = "http://localhost:5234/login"
POLL_SEC = 1
MAX_WAIT_SEC = 1200  # 20 min


async def main():
	# clear stale sentinels
	for p in (DONE, ABORT):
		if p.exists():
			p.unlink()
	print(f"[capture] storage will save to {STORAGE}", flush=True)
	print(f"[capture] DONE sentinel  = touch {DONE}", flush=True)
	print(f"[capture] ABORT sentinel = touch {ABORT}", flush=True)
	async with AuthCaptureSession(STORAGE, headless=False) as session:
		print(f"[capture] navigating to {LOGIN_URL} (once)...", flush=True)
		await session._cdp_navigate(LOGIN_URL)
		print("[capture] LOG IN in the browser window now.", flush=True)
		print(f"[capture] when done, run: touch {DONE}", flush=True)
		elapsed = 0
		aborted = False
		while elapsed < MAX_WAIT_SEC:
			if DONE.exists():
				print("[capture] DONE sentinel — saving state", flush=True)
				DONE.unlink()
				break
			if ABORT.exists():
				print("[capture] ABORT sentinel — stopping without save guarantees", flush=True)
				ABORT.unlink()
				aborted = True
				break
			await asyncio.sleep(POLL_SEC)
			elapsed += POLL_SEC
		else:
			print("[capture] timed out waiting for sentinel", flush=True)
		if not aborted:
			print(f"[capture] {elapsed}s elapsed, stopping session...", flush=True)
	# state written on context exit
	if STORAGE.exists():
		size = STORAGE.stat().st_size
		print(f"[capture] OK saved {STORAGE} ({size} bytes)", flush=True)
		if size < 100:
			print("[capture] WARN file is small — may be empty. Did login actually complete?", flush=True)
	else:
		print(f"[capture] WARN state did NOT land at {STORAGE}", flush=True)


if __name__ == "__main__":
	asyncio.run(main())
