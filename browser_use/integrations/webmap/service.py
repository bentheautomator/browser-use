"""
Webmap integration for browser-use.

Provides semantic page understanding using webmap's LLM-optimized extraction.
This integrates webmap as an alternative or supplementary page analysis tool.

The integration supports two modes:
1. Shared browser (preferred): Uses browser-use's page content via --stdin
   - No duplicate browser spawned
   - Exact same page state as browser-use sees
   - Lower resource usage

2. Standalone (fallback): Spawns webmap's own browser
   - Used when no browser session is available
   - Independent scanning

Usage:
    from browser_use.integrations.webmap import WebmapExtractor

    extractor = WebmapExtractor()

    # Shared browser mode (preferred)
    result = await extractor.scan_current_page(browser_session)

    # Standalone mode (fallback)
    result = await extractor.scan(url)
"""

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from browser_use.browser.session import BrowserSession

logger = logging.getLogger(__name__)


# ARIA roles considered "interactive" for AX-tree fallback enrichment.
# Kept tight on purpose — generic roles like "group" or "region" produce noise.
INTERACTIVE_AX_ROLES: frozenset[str] = frozenset(
	{
		'button',
		'link',
		'textbox',
		'searchbox',
		'combobox',
		'listbox',
		'checkbox',
		'radio',
		'switch',
		'slider',
		'spinbutton',
		'menuitem',
		'menuitemcheckbox',
		'menuitemradio',
		'tab',
		'option',
	}
)


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

	page_type: str = ''
	"""Detected page type (home, login, pricing, etc.)."""

	page_purpose: str = ''
	"""Inferred page purpose."""

	ax_interactives: list[dict] = field(default_factory=list)
	"""AX tree fallback: list of {role, name, selector_hint} for interactive nodes.

    Populated only when webmap returned 0 interactives AND the page actually has
    accessible interactive elements (typical for SPAs mid-hydration)."""

	enriched_via_ax_tree: bool = False
	"""True if ax_interactives was populated as a fallback."""

	@classmethod
	def from_agent_format(cls, raw: str, url: str) -> 'WebmapResult':
		"""Parse webmap agent format into structured result."""
		result = cls(raw=raw, url=url)

		for line in raw.strip().split('\n'):
			if line.startswith('P:'):
				# Page info: P:type|purpose|auth|position
				parts = line[2:].split('|')
				if len(parts) >= 2:
					result.page_type = parts[0]
					result.page_purpose = parts[1]

			elif line.startswith('I'):
				# Interactive count
				try:
					result.interactive_count = int(line[1:].split()[0])
				except (ValueError, IndexError):
					pass

			elif line.startswith('B:'):
				# Buttons: B:selector>label,selector>label,...
				button_data = line[2:]
				for btn in button_data.split(','):
					if '>' in btn:
						label = btn.split('>')[-1].strip()
						if label:
							result.buttons.append(label)

			elif line.startswith('L:'):
				# Links
				link_data = line[2:].replace('nav>', '')
				result.links = [link.strip() for link in link_data.split(',') if link.strip()]

			elif line.startswith('K:'):
				# Keywords
				result.keywords = [k.strip() for k in line[2:].split(',') if k.strip()]

			elif line.startswith('L') and ':' in line and line[1].isdigit():
				# Layout regions: L3:region1,region2,...
				parts = line.split(':', 1)
				if len(parts) == 2:
					result.regions = [r.strip() for r in parts[1].split(',') if r.strip()]

			elif line.startswith('~'):
				# Stats line: ~22i,3l,0x,3v|s:hash|...
				stats = line[1:].split('|')[0]
				for stat in stats.split(','):
					if stat.endswith('i'):
						try:
							result.inputs = int(stat[:-1])
						except ValueError:
							pass
					elif stat.endswith('x'):
						try:
							result.forms = int(stat[:-1])
						except ValueError:
							pass

		return result

	def to_prompt_context(self) -> str:
		"""Format result as context for LLM prompts."""
		lines = [
			f'Page: {self.url}',
			f'Type: {self.page_type} - {self.page_purpose}',
			f'Interactive elements: {self.interactive_count}',
		]

		if self.buttons:
			lines.append(f'Buttons: {", ".join(self.buttons[:10])}')
			if len(self.buttons) > 10:
				lines.append(f'  ... and {len(self.buttons) - 10} more buttons')

		if self.links:
			lines.append(f'Navigation: {", ".join(self.links[:10])}')

		if self.forms:
			lines.append(f'Forms: {self.forms}')

		if self.inputs:
			lines.append(f'Input fields: {self.inputs}')

		if self.keywords:
			lines.append(f'Keywords: {", ".join(self.keywords[:10])}')

		return '\n'.join(lines)


class WebmapExtractor:
	"""
	Webmap-based page extraction for browser-use.

	Provides semantic page understanding using webmap's LLM-optimized format.
	"""

	def __init__(
		self,
		webmap_path: str | None = None,
		timeout_ms: int = 30000,
		format: str = 'agent',
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
		"""
		Find webmap CLI in common locations.

		Order matters: a direct `webmap` on PATH (bash wrapper or installed
		global) is faster + more deterministic than `npx`, which falls back to
		a registry lookup when the package isn't linked locally (and breaks
		offline / private-package setups).
		"""
		direct = shutil.which('webmap')
		if direct:
			return direct
		if shutil.which('npx'):
			return 'npx'
		if shutil.which('pnpx'):
			return 'pnpx'
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
		cache_key = f'{url}:{format or self.format}'
		if use_cache and cache_key in self._cache:
			return self._cache[cache_key]

		stdout, stderr, returncode = await self._run_webmap('scan', url, format=format or self.format)

		if returncode != 0:
			logger.warning(f'webmap scan failed: {stderr}')
			return WebmapResult(raw=f'Error: {stderr}', url=url)

		result = WebmapResult.from_agent_format(stdout, url)
		self._cache[cache_key] = result
		return result

	async def scan_current_page(
		self,
		browser_session: 'BrowserSession',
		format: str | None = None,
	) -> WebmapResult:
		"""
		Scan the current page in the browser session using shared browser.

		This is the preferred method - it uses the browser-use page's HTML content
		directly via webmap's --stdin flag, avoiding a duplicate browser spawn.

		Args:
		    browser_session: Active browser session.
		    format: Override output format.

		Returns:
		    WebmapResult with parsed output.
		"""
		url = await self._get_current_url(browser_session)
		if not url:
			return WebmapResult(raw='Error: Could not get current URL', url='unknown')

		# Get HTML content from the shared browser
		html = await self._get_page_content(browser_session)
		if not html:
			logger.warning('Could not get page content, falling back to URL scan')
			return await self.scan(url, format=format)

		# Use stdin mode - pass HTML directly to webmap
		result = await self.scan_html(html, url, format=format)

		# AX tree fallback: SPAs often render hydration shell with 0 webmap
		# interactives even though the AX tree exposes real buttons/links/inputs.
		# Enrich result from CDP Accessibility.getFullAXTree when this happens.
		if result.interactive_count == 0 and not result.raw.startswith('Error:'):
			ax_nodes = await self._get_ax_interactives(browser_session)
			if ax_nodes:
				result.ax_interactives = ax_nodes
				result.interactive_count = len(ax_nodes)
				result.enriched_via_ax_tree = True
				logger.info(f'🦾 AX tree fallback enriched result: {len(ax_nodes)} interactives recovered for {url}')

		return result

	async def scan_html(
		self,
		html: str,
		url: str,
		format: str | None = None,
		use_cache: bool = True,
	) -> WebmapResult:
		"""
		Scan HTML content directly (shared browser mode).

		This is used when you already have the HTML from another browser controller.
		webmap uses --stdin to receive the HTML instead of navigating.

		Args:
		    html: HTML content to scan.
		    url: Base URL for resolving relative links.
		    format: Override output format.
		    use_cache: Whether to use cached results.

		Returns:
		    WebmapResult with parsed output.
		"""
		fmt = format or self.format
		cache_key = f'html:{hash(html)}:{url}:{fmt}'
		if use_cache and cache_key in self._cache:
			return self._cache[cache_key]

		stdout, stderr, returncode = await self._run_webmap_stdin(html, url, format=fmt)

		if returncode != 0:
			logger.warning(f'webmap stdin scan failed: {stderr}')
			return WebmapResult(raw=f'Error: {stderr}', url=url)

		result = WebmapResult.from_agent_format(stdout, url)
		self._cache[cache_key] = result
		return result

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
		stdout, stderr, returncode = await self._run_webmap('refresh', url, format=format or self.format)

		if returncode != 0:
			return f'Error: {stderr}'
		return stdout

	async def _run_webmap(
		self,
		command: str,
		url: str,
		format: str = 'agent',
		timeout: int | None = None,
	) -> tuple[str, str, int]:
		"""Execute webmap CLI command (spawns its own browser)."""
		if not self.webmap_path:
			return '', 'webmap CLI not found. Install with: npm install -g @bentheautomator/webmap', 1

		args = [self.webmap_path]
		if self.webmap_path in ('npx', 'pnpx'):
			args.append('@bentheautomator/webmap')

		args.extend([command, url, '-o', format])

		if timeout is None:
			timeout = self.timeout_ms

		args.extend(['--timeout', str(timeout)])

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
		except TimeoutError:
			return '', 'webmap execution timed out', 1
		except Exception as e:
			return '', str(e), 1

	async def _run_webmap_stdin(
		self,
		html: str,
		url: str,
		format: str = 'agent',
		timeout: int | None = None,
	) -> tuple[str, str, int]:
		"""
		Execute webmap CLI with HTML via stdin (shared browser mode).

		This avoids spawning a duplicate browser - webmap receives the HTML
		directly and uses page.setContent() internally.
		"""
		if not self.webmap_path:
			return '', 'webmap CLI not found. Install with: npm install -g @bentheautomator/webmap', 1

		args = [self.webmap_path]
		if self.webmap_path in ('npx', 'pnpx'):
			args.append('@bentheautomator/webmap')

		# Use --stdin flag to receive HTML from stdin
		args.extend(['scan', url, '--stdin', '-o', format])

		if timeout is None:
			timeout = self.timeout_ms

		args.extend(['--timeout', str(timeout)])

		try:
			proc = await asyncio.create_subprocess_exec(
				*args,
				stdin=asyncio.subprocess.PIPE,
				stdout=asyncio.subprocess.PIPE,
				stderr=asyncio.subprocess.PIPE,
			)
			# Pass HTML content via stdin
			stdout, stderr = await asyncio.wait_for(
				proc.communicate(input=html.encode('utf-8')),
				timeout=timeout / 1000 + 5,  # Add buffer
			)
			return stdout.decode(), stderr.decode(), proc.returncode or 0
		except TimeoutError:
			return '', 'webmap execution timed out', 1
		except Exception as e:
			return '', str(e), 1

	async def _get_current_url(self, browser_session: 'BrowserSession') -> str | None:
		"""Get current URL from browser session via CDP."""
		try:
			url = await browser_session.get_current_page_url()
			if url and url != 'about:blank':
				return url
		except Exception as e:
			logger.warning(f'Failed to get current URL: {e}')
		return None

	async def _get_page_content(self, browser_session: 'BrowserSession') -> str | None:
		"""Get HTML content from browser session via CDP (DOM.getOuterHTML on document root)."""
		try:
			cdp_session = await browser_session.get_or_create_cdp_session(target_id=None, focus=False)
			if not cdp_session:
				return None
			doc = await cdp_session.cdp_client.send.DOM.getDocument(
				params={},
				session_id=cdp_session.session_id,
			)
			if not doc or 'root' not in doc:
				return None
			result = await cdp_session.cdp_client.send.DOM.getOuterHTML(
				params={'nodeId': doc['root']['nodeId']},
				session_id=cdp_session.session_id,
			)
			return result.get('outerHTML') if result else None
		except Exception as e:
			logger.warning(f'Failed to get page content: {e}')
		return None

	async def _get_ax_interactives(self, browser_session: 'BrowserSession') -> list[dict]:
		"""
		Fetch interactive nodes from the AX (accessibility) tree via CDP.

		Used as a fallback when webmap's HTML-based scan returns 0 interactives —
		typical for SPAs where the visible page is rendered via portals, shadow
		DOM, or post-hydration React subtrees that the static outerHTML misses
		but the browser's accessibility tree exposes.

		Returns a list of {role, name, node_id} dicts for nodes whose role is
		in INTERACTIVE_AX_ROLES. Returns [] on failure (never raises).
		"""
		try:
			cdp_session = await browser_session.get_or_create_cdp_session(target_id=None, focus=False)
			if not cdp_session:
				return []
			await cdp_session.cdp_client.send.Accessibility.enable(
				session_id=cdp_session.session_id,
			)
			tree = await cdp_session.cdp_client.send.Accessibility.getFullAXTree(
				params={},
				session_id=cdp_session.session_id,
			)
			nodes = tree.get('nodes', []) if tree else []
			out: list[dict] = []
			for node in nodes:
				role_obj = node.get('role') or {}
				role = role_obj.get('value') if isinstance(role_obj, dict) else None
				if role not in INTERACTIVE_AX_ROLES:
					continue
				if node.get('ignored'):
					continue
				name_obj = node.get('name') or {}
				name = name_obj.get('value') if isinstance(name_obj, dict) else None
				out.append(
					{
						'role': role,
						'name': (name or '').strip(),
						'node_id': node.get('nodeId') or node.get('backendDOMNodeId'),
					}
				)
			return out
		except Exception as e:
			logger.warning(f'AX tree fallback failed: {e}')
			return []

	def clear_cache(self):
		"""Clear the result cache."""
		self._cache.clear()


# Convenience functions for quick scans
async def webmap_scan(url: str, format: str = 'agent') -> WebmapResult:
	"""Quick scan a URL with webmap (spawns its own browser)."""
	extractor = WebmapExtractor()
	return await extractor.scan(url, format=format)


async def webmap_scan_html(html: str, url: str, format: str = 'agent') -> WebmapResult:
	"""
	Quick scan HTML content with webmap (shared browser mode).

	Use this when you already have HTML from another browser controller.

	Args:
	    html: HTML content to scan.
	    url: Base URL for resolving relative links.
	    format: Output format (agent, json, markdown, w3).

	Returns:
	    WebmapResult with parsed output.
	"""
	extractor = WebmapExtractor()
	return await extractor.scan_html(html, url, format=format)


async def webmap_context(url: str) -> str:
	"""Get webmap context string for LLM prompts."""
	result = await webmap_scan(url)
	return result.to_prompt_context()


async def webmap_context_html(html: str, url: str) -> str:
	"""Get webmap context string from HTML (shared browser mode)."""
	result = await webmap_scan_html(html, url)
	return result.to_prompt_context()
