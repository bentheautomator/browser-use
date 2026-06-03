"""
Webmap Integration for Browser Use
Provides semantic page understanding using webmap's LLM-optimized extraction.
This integration enables agents to understand page structure and available actions.

Usage:
    from browser_use.integrations.webmap import WebmapExtractor, register_webmap_actions

    # Option 1: Register webmap actions with tools
    tools = Tools()
    register_webmap_actions(tools)

    # Option 2: Use the extractor directly
    extractor = WebmapExtractor()
    result = await extractor.scan("https://example.com")
    print(result.to_prompt_context())

    # Option 3: Quick scan helper
    from browser_use.integrations.webmap import webmap_scan
    result = await webmap_scan("https://example.com")
"""

from .actions import register_webmap_actions
from .service import (
    WebmapExtractor,
    WebmapResult,
    webmap_context,
    webmap_context_html,
    webmap_scan,
    webmap_scan_html,
)

__all__ = [
    'WebmapExtractor',
    'WebmapResult',
    'register_webmap_actions',
    'webmap_scan',
    'webmap_scan_html',
    'webmap_context',
    'webmap_context_html',
]
