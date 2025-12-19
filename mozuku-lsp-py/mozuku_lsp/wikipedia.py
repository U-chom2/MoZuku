"""Wikipedia summary fetcher with caching."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

# Cache settings
CACHE_TTL_SECONDS = 15 * 60  # 15 minutes
MAX_CACHE_SIZE = 100

# Wikipedia API settings
WIKIPEDIA_API_BASE = "https://ja.wikipedia.org/api/rest_v1/page/summary/"
REQUEST_TIMEOUT = 5.0


@dataclass
class CacheEntry:
    """Cache entry for Wikipedia summary."""

    content: str
    response_code: int
    timestamp: float


# Global cache
_cache: dict[str, CacheEntry] = {}


def _is_debug_enabled() -> bool:
    """Check if debug mode is enabled."""
    return os.environ.get("MOZUKU_DEBUG") is not None


def get_japanese_error_message(status_code: int) -> str:
    """Get Japanese error message for HTTP status code."""
    messages = {
        404: "該当する記事が見つかりませんでした",
        500: "サーバーエラーが発生しました",
        503: "サービスが一時的に利用できません",
        429: "リクエスト制限に達しました。しばらくお待ちください",
    }
    return messages.get(status_code, f"エラーが発生しました (HTTP {status_code})")


def get_cached_entry(query: str) -> CacheEntry | None:
    """Get cached Wikipedia entry if available and not expired.

    Args:
        query: Search query

    Returns:
        Cached entry or None
    """
    entry = _cache.get(query)
    if entry is None:
        return None

    # Check if expired
    if time.time() - entry.timestamp > CACHE_TTL_SECONDS:
        del _cache[query]
        return None

    return entry


def cache_entry(query: str, content: str, response_code: int) -> None:
    """Cache Wikipedia entry.

    Args:
        query: Search query
        content: Response content
        response_code: HTTP response code
    """
    # Evict old entries if cache is full
    if len(_cache) >= MAX_CACHE_SIZE:
        oldest_key = min(_cache.keys(), key=lambda k: _cache[k].timestamp)
        del _cache[oldest_key]

    _cache[query] = CacheEntry(
        content=content,
        response_code=response_code,
        timestamp=time.time(),
    )


async def fetch_summary_async(query: str) -> CacheEntry:
    """Fetch Wikipedia summary asynchronously.

    Args:
        query: Search query

    Returns:
        Cache entry with response
    """
    # Check cache first
    cached = get_cached_entry(query)
    if cached is not None:
        return cached

    url = f"{WIKIPEDIA_API_BASE}{query}"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "MoZuku-LSP/1.0 (Japanese NLP Language Server)",
                },
            )

            if response.status_code == 200:
                data = response.json()
                extract = data.get("extract", "")
                # Limit length
                if len(extract) > 500:
                    extract = extract[:500] + "..."
                cache_entry(query, extract, 200)

                if _is_debug_enabled():
                    import sys

                    print(f"[DEBUG] Wikipedia fetch success: {query}", file=sys.stderr)

                return CacheEntry(
                    content=extract, response_code=200, timestamp=time.time()
                )
            else:
                error_msg = get_japanese_error_message(response.status_code)
                cache_entry(query, error_msg, response.status_code)

                if _is_debug_enabled():
                    import sys

                    print(
                        f"[DEBUG] Wikipedia fetch failed: {query}, status={response.status_code}",
                        file=sys.stderr,
                    )

                return CacheEntry(
                    content=error_msg,
                    response_code=response.status_code,
                    timestamp=time.time(),
                )

    except httpx.TimeoutException:
        error_msg = "リクエストがタイムアウトしました"
        cache_entry(query, error_msg, 408)
        return CacheEntry(content=error_msg, response_code=408, timestamp=time.time())
    except Exception as e:
        error_msg = f"エラーが発生しました: {e}"
        cache_entry(query, error_msg, 500)
        return CacheEntry(content=error_msg, response_code=500, timestamp=time.time())


def fetch_summary_sync(query: str) -> CacheEntry:
    """Fetch Wikipedia summary synchronously.

    Args:
        query: Search query

    Returns:
        Cache entry with response
    """
    # Check cache first
    cached = get_cached_entry(query)
    if cached is not None:
        return cached

    url = f"{WIKIPEDIA_API_BASE}{query}"

    try:
        with httpx.Client() as client:
            response = client.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "MoZuku-LSP/1.0 (Japanese NLP Language Server)",
                },
            )

            if response.status_code == 200:
                data = response.json()
                extract = data.get("extract", "")
                # Limit length
                if len(extract) > 500:
                    extract = extract[:500] + "..."
                cache_entry(query, extract, 200)

                if _is_debug_enabled():
                    import sys

                    print(f"[DEBUG] Wikipedia fetch success: {query}", file=sys.stderr)

                return CacheEntry(
                    content=extract, response_code=200, timestamp=time.time()
                )
            else:
                error_msg = get_japanese_error_message(response.status_code)
                cache_entry(query, error_msg, response.status_code)

                if _is_debug_enabled():
                    import sys

                    print(
                        f"[DEBUG] Wikipedia fetch failed: {query}, status={response.status_code}",
                        file=sys.stderr,
                    )

                return CacheEntry(
                    content=error_msg,
                    response_code=response.status_code,
                    timestamp=time.time(),
                )

    except httpx.TimeoutException:
        error_msg = "リクエストがタイムアウトしました"
        cache_entry(query, error_msg, 408)
        return CacheEntry(content=error_msg, response_code=408, timestamp=time.time())
    except Exception as e:
        error_msg = f"エラーが発生しました: {e}"
        cache_entry(query, error_msg, 500)
        return CacheEntry(content=error_msg, response_code=500, timestamp=time.time())


def prefetch_summary(query: str) -> None:
    """Start prefetching Wikipedia summary in background.

    Args:
        query: Search query
    """
    # Check if already cached
    if get_cached_entry(query) is not None:
        return

    # Run in background
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(fetch_summary_async(query))
        else:
            # If no event loop is running, use thread
            import threading

            thread = threading.Thread(target=fetch_summary_sync, args=(query,))
            thread.daemon = True
            thread.start()
    except RuntimeError:
        # No event loop, use thread
        import threading

        thread = threading.Thread(target=fetch_summary_sync, args=(query,))
        thread.daemon = True
        thread.start()
