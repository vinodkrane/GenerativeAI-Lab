import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from app.core.config import settings


@pytest.mark.asyncio
async def test_health_check_is_public():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_chat_requires_auth():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            json={"session_id": "s1", "user_id": "u1", "message": "hello"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_happy_path(monkeypatch):
    from app import main

    async def fake_ainvoke(payload):
        return {
            "messages": [
                *payload["messages"],
                AIMessage(content="Hello! How can I help?"),
            ],
            "tool_calls": [],
        }

    monkeypatch.setattr("app.services.chat_service.chat_graph.ainvoke", fake_ainvoke)

    async def fake_get_history(session_id):
        return []

    async def fake_append_turn(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.chat_service.state_manager.get_history", fake_get_history
    )
    monkeypatch.setattr(
        "app.services.chat_service.state_manager.append_turn", fake_append_turn
    )

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            json={"session_id": "s1", "user_id": "u1", "message": "hello there"},
            headers={"X-API-Key": settings.api_key},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Hello! How can I help?"
    assert body["session_id"] == "s1"
