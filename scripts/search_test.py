
import asyncio
from app.search import SearchQuery, build_search_manager

async def main() -> None:
    m = build_search_manager("duckduckgo")
    r = await m.search(SearchQuery(text="current gold rate india", max_results=3))
    print(f"provider: {r.provider}")
    print(f"results: {len(r.results)}")
    for item in r.results:
        print(f"  - {item.title[:80]}")
        print(f"    {item.url}")
        print(f"    {item.snippet[:120]}")
    print()
    print(f"reference block present: {r.has_reference}")

asyncio.run(main())

# py -m scripts.search_test