import asyncio
from app.scrapers.detran_rj import DetranRJScraper

async def test():
    scraper = DetranRJScraper()
    # Test with a dummy placa (might fail if not real, but we can see the logs)
    # The user provided LKZ2945 in the prompt
    res = await scraper.get_cadastro_data("LKZ2945")
    print(res)

if __name__ == "__main__":
    if asyncio.get_event_loop_policy().__class__.__name__ == 'WindowsProactorEventLoopPolicy':
        pass
    else:
        import sys
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    asyncio.run(test())
