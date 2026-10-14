"""End-to-end probe of the search chain."""
import asyncio
from app.config.settings import load_settings
from app.search.manager import SearchManager
from app.search.providers import build_search_registry
from app.search.models import SearchQuery


async def main() -> None:
    settings = load_settings()
    print("Provider order:", settings.search_provider_order)
    print("SearXNG URL:  ", settings.searxng_base_url)

    registry = build_search_registry()
    manager = SearchManager(registry=registry, settings=settings)

    query = SearchQuery(text="gold rate chennai today", max_results=5)
    try:
        response = await manager.search(query)
        print(f"\nProvider used: {response.provider}")
        print(f"Results:       {len(response.results)}")
        for r in response.results[:3]:
            print(f"  - {r.title}\n    {r.url}")
    except Exception as exc:
        print(f"\nSearch FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())