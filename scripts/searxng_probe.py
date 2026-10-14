"""Direct probe of the SearXNG provider — bypasses SearchManager."""
import asyncio
from app.search.providers.searxng import SearXNGProvider
from app.search.models import SearchQuery


async def main() -> None:
    provider = SearXNGProvider(base_url="http://localhost:8888")

    query = SearchQuery(text="gold rate chennai today")
    response = await provider.search(query)

    print(f"Provider:  {response.provider}")
    print(f"Results:   {len(response.results)}")
    print()
    for r in response.results[:5]:
        print(f"  • {r.title}")
        print(f"    {r.url}")
        if getattr(r, "snippet", None):
            print(f"    {r.snippet[:140]}")
        print()


if __name__ == "__main__":
    asyncio.run(main())