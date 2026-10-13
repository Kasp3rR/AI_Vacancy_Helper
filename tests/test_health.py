import asyncio

import httpx

from app.main import create_app


def test_health_and_openapi() -> None:
    async def check() -> None:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            assert "/health" in (await client.get("/openapi.json")).json()["paths"]

    asyncio.run(check())
