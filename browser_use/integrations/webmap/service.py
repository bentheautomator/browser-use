"""
Webmap integration for browser-use.

Provides semantic page understanding using webmap's LLM-optimized extraction.
This integrates webmap as an alternative or supplementary page analysis tool.

Usage:
    from browser_use.integrations.webmap import WebmapExtractor

    extractor = WebmapExtractor()
    result = await extractor.scan(url)
    # Returns webmap's agent format output
"""

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from browser_use.browser.session import BrowserSession

logger = logging.getLogger(__name__)


@dataclass
class WebmapResult:
    """Result from a webmap scan."""

    raw: str
    """Raw webmap output in agent format."""

    url: str
    """URL that was scanned."""

    buttons: list[str] = field(default_factory=list)
    """Extracted button labels."""

    links: list[str] = field(default_factory=list)
    """Extracted link texts."""

    forms: int = 0
    """Number of forms detected."""

    inputs: int = 0
    """Number of input fields."""

    interactive_count: int = 0
    """Total interactive elements."""

    regions: list[str] = field(default_factory=list)
    """Layout regions detected."""

    keywords: list[str] = field(default_factory=list)
    """Page keywords."""

    page_type: str = ""
    """Detected page type (home, login, pricing, etc.)."""

    page_purpose: str = ""
    """Inferred page purpose."""

    @classmethod
    def from_agent_format(cls, raw: str, url: str) -> "WebmapResult":
        """Parse webmap agent format into structured result."""
        result = cls(raw=raw, url=url)

        for line in raw.strip().split("\n"):
            if line.startswith("P:"):
                # Page info: P:type|purpose|auth|position
                parts = line[2:].split("|")
                if len(parts) >= 2:
                    result.page_type = parts[0]
                    result.page_purpose = parts[1]

            elif line.startswith("I"):
                # Interactive count
                try:
                    result.interactive_count = int(line[1:].split()[0])
                except (ValueError, IndexError):
                    pass

            elif line.startswith("B:"):
                # Buttons: B:selector>label,selector>label,...
                button_data = line[2:]
                for btn in button_data.split(","):
                    if ">" in btn:
                        label = btn.split(">")[-1].strip()
                        if label:
                            result.buttons.append(label)

            elif line.startswith("L:"):
                # Links
                link_data = line[2:].replace("nav>", "")
                result.links = [l.strip() for l in link_data.split(",") if l.strip()]

            elif line.startswith("K:"):
                # Keywords
                result.keywords = [k.strip() for k in line[2:].split(",") if k.strip()]

            elif line.startswith("L") and ":" in line and line[1].isdigit():
                # Layout regions: L3:region1,region2,...
                parts = line.split(":", 1)
                if len(parts) == 2:
                    result.regions = [r.strip() for r in parts[1].split(",") if r.strip()]

            elif line.startswith("~"):
                # Stats line: ~22i,3l,0x,3v|s:hash|...
                stats = line[1:].split("|")[0]
                for stat in stats.split(","):
                    if stat.endswith("i"):
                        try:
                            result.inputs = int(stat[:-1])
                        except ValueError:
                            pass
                    elif stat.endswith("x"):
                        try:
                            result.forms = int(stat[:-1])
                        except ValueError:
                            pass

        return result

    def to_prompt_context(self) -> str:
        """Format result as context for LLM prompts."""
        lines = [
            f"Page: {self.url}",
            f"Type: {self.page_type} - {self.page_purpose}",
            f"Interactive elements: {self.interactive_count}",
        ]

        if self.buttons:
            lines.append(f"Buttons: {', '.join(self.buttons[:10])}")
            if len(self.buttons) > 10:
                lines.append(f"  ... and {len(self.buttons) - 10} more buttons")

        if self.links:
            lines.append(f"Navigation: {', '.join(self.links[:10])}")

        if self.forms:
            lines.append(f"Forms: {self.forms}")

        if self.inputs:
            lines.append(f"Input fields: {self.inputs}")

        if self.keywords:
            lines.append(f"Keywords: {', '.join(self.keywords[:10])}")

        return "\n".join(lines)


class WebmapExtractor:
    """
    Webmap-based page extraction for browser-use.

    Provides semantic page understanding using webmap's LLM-optimized format.
    """

    def __init__(
        self,
        webmap_path: str | None = None,
        timeout_ms: int = 30000,
        format: str = "agent",
    ):
        """
        Initialize the webmap extractor.

        Args:
            webmap_path: Path to webmap CLI. Auto-detected if not provided.
            timeout_ms: Timeout for webmap execution in milliseconds.
            format: Output format (agent, json, markdown, w3).
        """
        self.webmap_path = webmap_path or self._find_webmap()
        self.timeout_ms = timeout_ms
        self.format = format
        self._cache: dict[str, WebmapResult] = {}

    def _find_webmap(self) -> str | None:
        """Find webmap CLI in common locations."""
        # Check for npx/pnpx
        if shutil.which("npx"):
            return "npx"
        if shutil.which("pnpx"):
            return "pnpx"
        return None

    async def scan(
        self,
        url: str,
        format: str | None = None,
        use_cache: bool = True,
    ) -> WebmapResult:
        """
        Scan a URL with webmap.

        Args:
            url: URL to scan.
            format: Override output format.
            use_cache: Whether to use cached results.

        Returns:
            WebmapResult with parsed output.
        """
        cache_key = f"{url}:{format or self.format}"
        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        stdout, stderr, returncode = await self._run_webmap(
            "scan", url, format=format or self.format
        )

        if returncode != 0:
            logger.warning(f"webmap scan failed: {stderr}")
            return WebmapResult(raw=f"Error: {stderr}", url=url)

        result = WebmapResult.from_agent_format(stdout, url)
        self._cache[cache_key] = result
        return result

    async def scan_current_page(
        self,
        browser_session: "BrowserSession",
        format: str | None = None,
    ) -> WebmapResult:
        """
        Scan the current page in the browser session.

        Args:
            browser_session: Active browser session.
            format: Override output format.

        Returns:
            WebmapResult with parsed output.
        """
        url = await self._get_current_url(browser_session)
        if not url:
            return WebmapResult(raw="Error: Could not get current URL", url="unknown")
        return await self.scan(url, format=format)

    async def diff(
        self,
        url: str,
        format: str | None = None,
    ) -> str:
        """
        Run webmap refresh to detect changes since last scan.

        Args:
            url: URL to check.
            format: Override output format.

        Returns:
            Diff output showing changes.
        """
        stdout, stderr, returncode = await self._run_webmap(
            "refresh", url, format=format or self.format
        )

        if returncode != 0:
            return f"Error: {stderr}"
        return stdout

    async def _run_webmap(
        self,
        command: str,
        url: str,
        format: str = "agent",
        timeout: int | None = None,
    ) -> tuple[str, str, int]:
        """Execute webmap CLI command."""
        if not self.webmap_path:
            return "", "webmap CLI not found. Install with: npm install -g @bentheautomator/webmap", 1

        args = [self.webmap_path]
        if self.webmap_path in ("npx", "pnpx"):
            args.append("@bentheautomator/webmap")

        args.extend([command, url, "-o", format])

        if timeout is None:
            timeout = self.timeout_ms

        args.extend(["--timeout", str(timeout)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout / 1000 + 5,  # Add buffer
            )
            return stdout.decode(), stderr.decode(), proc.returncode or 0
        except asyncio.TimeoutError:
            return "", "webmap execution timed out", 1
        except Exception as e:
            return "", str(e), 1

    async def _get_current_url(self, browser_session: "BrowserSession") -> str | None:
        """Get current URL from browser session."""
        try:
            if hasattr(browser_session, "current_url"):
                return browser_session.current_url
            if hasattr(browser_session, "page") and browser_session.page:
                return browser_session.page.url
            if hasattr(browser_session, "get_current_url"):
                return await browser_session.get_current_url()
        except Exception as e:
            logger.warning(f"Failed to get current URL: {e}")
        return None

    def clear_cache(self):
        """Clear the result cache."""
        self._cache.clear()


# Convenience function for quick scans
async def webmap_scan(url: str, format: str = "agent") -> WebmapResult:
    """Quick scan a URL with webmap."""
    extractor = WebmapExtractor()
    return await extractor.scan(url, format=format)


async def webmap_context(url: str) -> str:
    """Get webmap context string for LLM prompts."""
    result = await webmap_scan(url)
    return result.to_prompt_context()
