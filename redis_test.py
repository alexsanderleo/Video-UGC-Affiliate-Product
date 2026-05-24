import asyncio
import redis.asyncio as async_redis
import redis

def test_sync():
    print("--- Testing Synchronous Redis Connection ---")
    urls = [
        "redis://127.0.0.1:6379/0",
        "redis://localhost:6379/0",
        "redis://localhost:6379",
        "redis://127.0.0.1:6379"
    ]
    for url in urls:
        print(f"Connecting to: {url} ...", end=" ")
        try:
            r = redis.from_url(url, socket_timeout=3.0)
            response = r.ping()
            print(f"SUCCESS! Response: {response}")
        except Exception as e:
            print(f"FAILED: {e}")

async def test_async():
    print("\n--- Testing Asynchronous Redis Connection ---")
    urls = [
        "redis://127.0.0.1:6379/0",
        "redis://localhost:6379/0"
    ]
    for url in urls:
        print(f"Connecting to: {url} ...", end=" ")
        try:
            r = async_redis.from_url(url, socket_timeout=3.0)
            response = await r.ping()
            print(f"SUCCESS! Response: {response}")
            await r.close()
        except Exception as e:
            print(f"FAILED: {e}")

if __name__ == "__main__":
    test_sync()
    asyncio.run(test_async())
