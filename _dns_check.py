import asyncio
import aiohttp

async def test():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://pro-api.coinmarketcap.com') as resp:
                print(f"Status: {resp.status}")
                # 401/403 is fine, it means we connected and DNS worked
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
