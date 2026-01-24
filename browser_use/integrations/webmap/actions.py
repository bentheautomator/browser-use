"""
Webmap Actions for Browser Use
Provides semantic page understanding using webmap's LLM-optimized extraction.
These actions give the agent structural awareness of pages without needing
to parse raw DOM.
"""

import logging

from pydantic import BaseModel, Field

from browser_use.agent.views import ActionResult
from browser_use.tools.service import Tools

from .service import WebmapExtractor, WebmapResult

logger = logging.getLogger(__name__)

# Global webmap extractor instance
_webmap_extractor: WebmapExtractor | None = None


class WebmapScanParams(BaseModel):
    """Parameters for scanning a page with webmap"""

    url: str = Field(
        default='',
        description='URL to scan. Leave empty to scan the current page.',
    )
    format: str = Field(
        default='agent',
        description='Output format: agent (compact), json (structured), markdown (readable)',
    )


class WebmapDiffParams(BaseModel):
    """Parameters for detecting page changes"""

    url: str = Field(
        description='URL to check for changes since last scan',
    )


def register_webmap_actions(
    tools: Tools,
    webmap_extractor: WebmapExtractor | None = None,
    timeout_ms: int = 30000,
) -> Tools:
    """
    Register webmap actions with the browser-use tools.

    Args:
        tools: The browser-use tools to register actions with
        webmap_extractor: Optional pre-configured WebmapExtractor instance
        timeout_ms: Timeout for webmap operations in milliseconds
    """
    global _webmap_extractor

    if webmap_extractor:
        _webmap_extractor = webmap_extractor
    else:
        _webmap_extractor = WebmapExtractor(timeout_ms=timeout_ms)

    @tools.registry.action(
        description=(
            'Scan a web page with webmap to understand its semantic structure. '
            'Returns page type, interactive elements (buttons, links, forms), '
            'navigation structure, and keywords. Use this to understand what '
            'actions are available on a page before interacting with it.'
        ),
        param_model=WebmapScanParams,
    )
    async def webmap_scan(params: WebmapScanParams) -> ActionResult:
        """Scan a page to understand its structure and available actions."""
        try:
            if _webmap_extractor is None:
                raise RuntimeError('Webmap extractor not initialized')

            url = params.url
            if not url:
                return ActionResult(
                    extracted_content='Error: URL is required for webmap scan',
                    long_term_memory='webmap_scan called without URL',
                )

            logger.info(f'🗺️ Scanning page with webmap: {url}')
            result = await _webmap_extractor.scan(url, format=params.format)

            if result.raw.startswith('Error:'):
                return ActionResult(
                    extracted_content=result.raw,
                    long_term_memory=f'webmap scan failed for {url}',
                )

            # Format the context for the agent
            context = result.to_prompt_context()

            # Build a detailed summary for long-term memory
            memory_parts = [f'Scanned {url}']
            if result.page_type:
                memory_parts.append(f'type={result.page_type}')
            if result.interactive_count:
                memory_parts.append(f'{result.interactive_count} interactive elements')
            if result.buttons:
                memory_parts.append(f'buttons: {", ".join(result.buttons[:5])}')
            if result.forms:
                memory_parts.append(f'{result.forms} forms')

            logger.info(f'🗺️ Webmap scan complete: {result.page_type}, {result.interactive_count} elements')

            return ActionResult(
                extracted_content=context,
                include_extracted_content_only_once=True,
                long_term_memory=' | '.join(memory_parts),
            )

        except Exception as e:
            logger.error(f'Error in webmap scan: {e}')
            return ActionResult(
                error=f'Webmap scan error: {str(e)}',
                long_term_memory=f'webmap_scan failed: {str(e)}',
            )

    @tools.registry.action(
        description=(
            'Detect changes on a web page since the last scan. '
            'Use this to monitor for dynamic content updates, new elements, '
            'or page state changes after performing actions.'
        ),
        param_model=WebmapDiffParams,
    )
    async def webmap_diff(params: WebmapDiffParams) -> ActionResult:
        """Detect page changes since last scan."""
        try:
            if _webmap_extractor is None:
                raise RuntimeError('Webmap extractor not initialized')

            logger.info(f'🗺️ Checking for page changes: {params.url}')
            diff_output = await _webmap_extractor.diff(params.url)

            if diff_output.startswith('Error:'):
                return ActionResult(
                    extracted_content=diff_output,
                    long_term_memory=f'webmap diff failed for {params.url}',
                )

            # Determine if changes were detected
            has_changes = bool(diff_output.strip()) and 'no changes' not in diff_output.lower()

            if has_changes:
                memory = f'Changes detected on {params.url}'
                logger.info(f'🗺️ Page changes detected on {params.url}')
            else:
                memory = f'No changes detected on {params.url}'
                logger.info(f'🗺️ No page changes on {params.url}')

            return ActionResult(
                extracted_content=diff_output if has_changes else 'No changes detected since last scan.',
                long_term_memory=memory,
            )

        except Exception as e:
            logger.error(f'Error in webmap diff: {e}')
            return ActionResult(
                error=f'Webmap diff error: {str(e)}',
                long_term_memory=f'webmap_diff failed: {str(e)}',
            )

    return tools
