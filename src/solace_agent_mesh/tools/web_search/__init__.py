"""Web search tools for MedExpert.

This module provides Google Search integration. For other search providers
(Exa, Brave, Tavily), please use the corresponding plugins from
medexpert-plugins repository.
"""

from .models import SearchSource, SearchResult, ImageResult
from .base import WebSearchTool
from .google_search import GoogleSearchTool
from .duckduckgo_search import DuckDuckGoSearchTool

__all__ = [
    "SearchSource",
    "SearchResult",
    "ImageResult",
    "WebSearchTool",
    "GoogleSearchTool",
    "DuckDuckGoSearchTool",
]