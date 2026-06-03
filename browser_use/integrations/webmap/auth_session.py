"""
Auth-aware BrowserSession helpers for webmap audits behind a login wall.

The pieces that do the real work already exist in browser-use:
    BrowserProfile(storage_state=<path>)  -> StorageStateWatchdog
    BrowserSession.export_storage_state() -> writes Playwright-format JSON

This module is the thin ergonomic layer on top — two context managers that
make "log in once, then run N webmap scans against authenticated pages" a
two-line pattern.

Capture flow (headful, interactive, once per audit):

    async with AuthCaptureSession("tmp/site.storage.json") as session:
        await session._cdp_navigate("https://site.example/login")
        input("press enter once you're logged in...")
    # state saved on context exit via StorageStateWatchdog -> BrowserStopEvent

Scan flow (headless, repeatable):

    async with AuthScanSession("tmp/site.storage.json") as session:
        await session._cdp_navigate("https://site.example/dashboard")
        result = await WebmapExtractor().scan_current_page(session)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession

logger = logging.getLogger(__name__)


@asynccontextmanager
async def AuthCaptureSession(
    storage_state_path: str | Path,
    headless: bool = False,
) -> AsyncIterator[BrowserSession]:
    """
    Headful BrowserSession that writes its storage_state to disk on exit.

    Use this once per site/account to capture cookies + localStorage after
    a real login. Subsequent runs of AuthScanSession against the same path
    will skip the login.

    Args:
        storage_state_path: where to write the Playwright-format storage_state
            JSON. Parent dir is created if missing.
        headless: default False so a real user can complete the login; pass
            True only when the login is fully scriptable (rare).
    """
    path = Path(storage_state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = BrowserProfile(headless=headless, storage_state=str(path))
    session = BrowserSession(browser_profile=profile)
    await session.start()
    try:
        logger.info(
            f"🔐 AuthCaptureSession started (headless={headless}); "
            f"state will be saved to {path} on exit"
        )
        yield session
    finally:
        # StorageStateWatchdog persists state on BrowserStopEvent.
        await session.stop()
        if path.exists():
            logger.info(f"🔐 AuthCaptureSession saved state to {path}")
        else:
            logger.warning(
                f"🔐 AuthCaptureSession exited without writing {path}; "
                "did the session leave its login screen?"
            )


@asynccontextmanager
async def AuthScanSession(
    storage_state_path: str | Path,
    headless: bool = True,
) -> AsyncIterator[BrowserSession]:
    """
    BrowserSession that restores cookies + localStorage from a saved file.

    The file must already exist — call AuthCaptureSession once first.
    Defaults to headless because typical use is "scan N pages quickly."
    """
    path = Path(storage_state_path)
    if not path.exists():
        raise FileNotFoundError(
            f"storage_state file not found: {path}. "
            "Run AuthCaptureSession first to capture login state."
        )
    profile = BrowserProfile(headless=headless, storage_state=str(path))
    session = BrowserSession(browser_profile=profile)
    await session.start()
    logger.info(f"🔐 AuthScanSession started from {path} (headless={headless})")
    try:
        yield session
    finally:
        await session.stop()
