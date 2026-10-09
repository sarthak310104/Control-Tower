"""Thin async wrapper over the coordinator's REST API. One instance per MCP process."""

from typing import Any

import httpx


class CoordinatorError(Exception):
    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        message = detail.get("message", detail) if isinstance(detail, dict) else detail
        super().__init__(f"coordinator returned {status}: {message}")


class CoordinatorUnavailable(Exception):
    pass


class CoordinatorClient:
    def __init__(self, base_url: str, timeout: float = 10, transport: httpx.AsyncBaseTransport | None = None):
        # `transport` lets tests point this at the FastAPI app in-process.
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self.base_url = base_url

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            resp = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise CoordinatorUnavailable(
                f"can't reach the coordinator at {self.base_url} ({exc.__class__.__name__}). "
                f"Is it running? Start it with: uvicorn app.main:app"
            ) from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise CoordinatorError(resp.status_code, detail)
        return resp.json()

    # projects / agents
    async def ensure_project(self, name: str, repo_root: str | None) -> dict:
        return await self._request("POST", "/projects", json={"name": name, "repo_root": repo_root})

    async def register_agent(self, project_id: int, name: str, role: str | None) -> dict:
        return await self._request("POST", "/agents", json={"project_id": project_id, "name": name, "role": role})

    async def list_agents(self, project_id: int) -> list[dict]:
        return await self._request("GET", "/agents", params={"project_id": project_id})

    # claims
    async def claim(self, body: dict) -> dict:
        return await self._request("POST", "/claims", json=body)

    async def check(self, body: dict) -> dict:
        return await self._request("POST", "/claims/check", json=body)

    async def release(self, claim_id: int, agent_id: int, note: str | None) -> dict:
        return await self._request(
            "POST", f"/claims/{claim_id}/release", json={"agent_id": agent_id, "release_note": note}
        )

    async def heartbeat_all(self, agent_id: int) -> dict:
        return await self._request("POST", "/claims/heartbeat", params={"agent_id": agent_id})

    async def list_claims(self, project_id: int, **filters) -> list[dict]:
        params = {"project_id": project_id, **{k: v for k, v in filters.items() if v is not None}}
        return await self._request("GET", "/claims", params=params)

    # messages / activity
    async def send_message(self, project_id: int, from_agent_id: int, to_agent_id: int | None, body: str) -> dict:
        return await self._request(
            "POST", "/messages",
            json={"project_id": project_id, "from_agent_id": from_agent_id, "to_agent_id": to_agent_id, "body": body},
        )

    async def inbox(self, project_id: int, agent_id: int, since_id: int) -> list[dict]:
        return await self._request(
            "GET", "/messages/inbox", params={"project_id": project_id, "agent_id": agent_id, "since_id": since_id}
        )

    async def activity(self, project_id: int, since_id: int, limit: int) -> list[dict]:
        return await self._request(
            "GET", "/activity", params={"project_id": project_id, "since_id": since_id, "limit": limit}
        )