"""How upstream OSWorld's code, run unmodified in the controller, reaches the guest server: upstream builds every URL as
``http://{vm_ip}:{server_port}``, so the controller runs it with ``vm_ip`` 127.0.0.1 and port 5000, where this forwarder
listens and sends each call on to the guest server's base URL (``REMOTE_VM_IP``, path prefix and all), with the headers
every call there carries (``REMOTE_SERVER_HEADERS``, the computer's control token).

The computer's Chrome DevTools port (9222) and VLC's HTTP port (8080), which upstream reaches the same way, aren't served
by the ``osworld-server`` app, so their loopback ports get refusers: each answers 501 and records that it was called,
for the controller to say what a failed setup or check needed (``Refusals``).

This file imports neither agent-env nor upstream OSWorld, so it is loaded by path in agent-env's tests."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

GUEST_HOST, GUEST_PORT = "127.0.0.1", 5000
#: The guest's other ports upstream calls, which the osworld-server app doesn't serve, by what they are.
REFUSED_PORTS = {9222: "the computer's Chrome DevTools port 9222", 8080: "VLC's HTTP port 8080"}
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
#: Setup downloads a task's files and a check reads large ones; a step allows each call up to 1800 s.
TIMEOUT_S = 1800.0
#: Not forwarded: the hop's own headers. A body is sent whole, with its length set again.
_DROPPED_REQUEST = frozenset({"host", "content-length", "connection", "keep-alive", "transfer-encoding", "te",
                              "upgrade", "proxy-authorization", "proxy-connection", "trailer"})
_DROPPED_RESPONSE = frozenset({"content-length", "connection", "keep-alive", "transfer-encoding", "content-encoding",
                               "upgrade", "trailer"})


def guest_target(base_url: str, path: str) -> str:
    """``path`` under the guest server's ``base_url``, its prefix kept."""
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def create_forwarder(base_url: str, headers: dict[str, str], client: httpx.AsyncClient) -> Starlette:
    """The app on the guest server's loopback port: every call sent to ``base_url`` with ``headers``, its answer
    returned as the guest server gave it, or 502 when the guest server can't be reached."""

    async def forward(request: Request) -> Response:
        sent = {k: v for k, v in request.headers.items() if k.lower() not in _DROPPED_REQUEST}
        sent.update(headers)
        try:
            answer = await client.request(request.method, guest_target(base_url, request.url.path),
                                          params=list(request.query_params.multi_items()), headers=sent,
                                          content=await request.body(), timeout=TIMEOUT_S)
        except httpx.HTTPError as e:
            return Response(f"the guest server is unreachable: {type(e).__name__}", status_code=502)
        return Response(answer.content, status_code=answer.status_code,
                        headers={k: v for k, v in answer.headers.items() if k.lower() not in _DROPPED_RESPONSE})

    return Starlette(routes=[Route("/{path:path}", forward, methods=METHODS)])


@dataclass
class Refusals:
    """The refused ports called, in order, so a call can tell which it needed."""

    _calls: list[int] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def mark(self, port: int) -> None:
        with self._lock:
            self._calls.append(port)

    def mark_count(self) -> int:
        with self._lock:
            return len(self._calls)

    def since(self, count: int) -> list[int]:
        """The refused ports called after ``mark_count`` was ``count``, each once."""
        with self._lock:
            return list(dict.fromkeys(self._calls[count:]))


def needed(ports: list[int]) -> str:
    """What a setup or check that called ``ports`` needed, for its error."""
    named = " and ".join(REFUSED_PORTS[port] for port in ports)
    return f"it needs {named}, which the osworld-server app doesn't expose; not supported by the public controller"


def create_refuser(port: int, refusals: Refusals) -> Starlette:
    """The app on one of ``REFUSED_PORTS``: every call answered 501, and every websocket (CDP's) closed, and recorded."""

    async def refuse(request: Request) -> Response:
        refusals.mark(port)
        return Response(needed([port]), status_code=501)

    async def refuse_socket(websocket: WebSocket) -> None:
        refusals.mark(port)
        await websocket.close(code=1011, reason=needed([port])[:120])

    return Starlette(routes=[Route("/{path:path}", refuse, methods=METHODS),
                             WebSocketRoute("/{path:path}", refuse_socket)])
