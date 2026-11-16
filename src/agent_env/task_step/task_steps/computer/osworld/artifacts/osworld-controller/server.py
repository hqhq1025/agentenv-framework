"""The ``osworld-controller`` image's HTTP API, which the ``osworld_*`` steps call: a task's setup (``POST /setup``) and
its checks (``POST /evaluate``, or ``POST /evaluate_async`` polled with ``GET /evaluate_result``), run by upstream
OSWorld's own ``SetupController``, getters and metrics (:mod:`runtime`) against the guest server beside it; ``GET
/configure`` answers once it's up. Every call must carry ``Authorization: Bearer <CONTROLLER_AUTH_TOKEN>``, refused 401
otherwise, since the controller's port may be reachable from the computer the agent drives.

It serves upstream OSWorld's checks only. An LLM-judge func (``agent_judge``...) and an OSWorld-V2 task (``/reset``,
``/initialize_task``, a ``osworld_v2_task_url``) are answered 501, for a controller image that serves them; a func or
getter upstream doesn't have is answered 422. One call runs at a time: upstream's controllers aren't thread-safe and
share a cache folder.

This file imports neither agent-env nor upstream OSWorld, so it is loaded by path in agent-env's tests."""
from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from typing import Any, Protocol

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger("osworld_controller")

#: The env the controller is started with, its contract, as agent-env's ``controller.CONTROLLER_ENV_KEYS`` names it:
#: the provider, which is ``remote``, the guest's OS type, the guest server's base URL with any path prefix, the JSON
#: headers every call to it carries, and the bearer token every call to the controller must carry.
PROVIDER_ENV, OS_TYPE_ENV, GUEST_URL_ENV, GUEST_HEADERS_ENV, TOKEN_ENV = (
    "PROVIDER", "OS_TYPE", "REMOTE_VM_IP", "REMOTE_SERVER_HEADERS", "CONTROLLER_AUTH_TOKEN")
CONTROLLER_ENV_KEYS = (PROVIDER_ENV, OS_TYPE_ENV, GUEST_URL_ENV, GUEST_HEADERS_ENV, TOKEN_ENV)
REMOTE_PROVIDER = "remote"
JUDGE_FUNC_PREFIX = "agent_judge"
JUDGE_UNSUPPORTED = ("LLM-judge funcs need a controller image that serves them; name it with "
                     "controller_image_artifact_id")
V2_UNSUPPORTED = ("OSWorld-V2 tasks need a controller image that serves /initialize_task; name it with "
                  "controller_image_artifact_id")
#: How much of an error an answer carries: the rest can be large and hold a task's content.
ERROR_CHARS = 2000
#: Finished async jobs kept for their caller to read.
KEPT_JOBS = 256


class UnknownCheck(LookupError):
    """A func or getter upstream OSWorld doesn't have."""


class Runtime(Protocol):
    def setup(self, config: list[dict]) -> None:
        """Run ``config`` on the guest; raises with what failed."""

    def evaluate(self, body: dict) -> float:
        """The score of one func over its result and expected; raises UnknownCheck for a func or getter upstream
        doesn't have."""


def bounded(error: BaseException | str) -> str:
    text = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    return text[:ERROR_CHARS]


def _unsupported(body: dict) -> str | None:
    func = body.get("func")
    if isinstance(func, str) and func.startswith(JUDGE_FUNC_PREFIX):
        return JUDGE_UNSUPPORTED
    if body.get("osworld_v2_task_url"):
        return V2_UNSUPPORTED
    return None


class _Bearer(BaseHTTPMiddleware):
    def __init__(self, app: Any, token: str):
        super().__init__(app)
        self.token = token.encode()

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        scheme, _, given = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode(), self.token):
            return JSONResponse({"detail": "a call to the controller must carry its bearer token"}, status_code=401)
        return await call_next(request)


def create_app(token: str, runtime: Runtime, *, os_type: str = "Ubuntu", osworld_commit: str = "") -> Starlette:
    """The controller's app, requiring ``token`` on every call and running checks on ``runtime``."""
    if not token.strip():
        raise ValueError(f"{TOKEN_ENV} is empty; the controller refuses to serve without a token")
    lock = asyncio.Lock()
    jobs: dict[str, dict] = {}
    running: set[asyncio.Task] = set()  # the loop keeps only weak references to its tasks

    async def run(call: Any, *args: Any) -> Any:
        async with lock:
            return await asyncio.to_thread(call, *args)

    async def body_of(request: Request) -> dict | Response:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"detail": "the body must be JSON"}, status_code=400)
        return body if isinstance(body, dict) else JSONResponse({"detail": "the body must be a JSON object"},
                                                                status_code=400)

    async def configure(request: Request) -> Response:
        return JSONResponse({"os_type": os_type, "osworld_commit": osworld_commit, "serves": ["setup", "evaluate"]})

    async def setup(request: Request) -> Response:
        body = await body_of(request)
        if isinstance(body, Response):
            return body
        config = body.get("config") or []
        if not isinstance(config, list):
            return JSONResponse({"detail": "config must be a list of setup steps"}, status_code=400)
        try:
            await run(runtime.setup, config)
        except Exception as e:
            logger.warning(f"/setup failed: {bounded(e)}")
            return JSONResponse({"success": False, "error": bounded(e)})
        return JSONResponse({"success": True, "steps": len(config)})

    async def graded(body: dict) -> dict:
        """One func's answer: its score, or a 0 with why it couldn't be checked."""
        try:
            score = await run(runtime.evaluate, body)
        except UnknownCheck:
            raise
        except Exception as e:
            logger.warning(f"/evaluate {body.get('func')!r} failed: {bounded(e)}")
            return {"score": 0.0, "error": True, "message": bounded(e)}
        return {"score": float(score), "error": False, "message": ""}

    def refused(body: dict) -> Response | None:
        if not isinstance(body.get("func"), str) or not body["func"]:
            return JSONResponse({"detail": "func must name a metric"}, status_code=400)
        if reason := _unsupported(body):
            return JSONResponse({"detail": reason}, status_code=501)
        return None

    async def evaluate(request: Request) -> Response:
        body = await body_of(request)
        if isinstance(body, Response):
            return body
        if (refusal := refused(body)) is not None:
            return refusal
        try:
            return JSONResponse(await graded(body))
        except UnknownCheck as e:
            return JSONResponse({"detail": str(e)}, status_code=422)

    async def evaluate_async(request: Request) -> Response:
        body = await body_of(request)
        if isinstance(body, Response):
            return body
        if (refusal := refused(body)) is not None:
            return refusal
        job_id = uuid.uuid4().hex
        jobs[job_id] = {"status": "running"}

        async def job() -> None:
            try:
                jobs[job_id] = {"status": "done", "result": await graded(body)}
            except UnknownCheck as e:
                jobs[job_id] = {"status": "error", "message": str(e)}
            for old in [k for k, v in jobs.items() if v["status"] != "running"][:-KEPT_JOBS]:
                jobs.pop(old, None)

        task = asyncio.get_running_loop().create_task(job())
        running.add(task)
        task.add_done_callback(running.discard)
        return JSONResponse({"job_id": job_id})

    async def evaluate_result(request: Request) -> Response:
        return JSONResponse(jobs.get(request.query_params.get("job_id", ""),
                                     {"status": "unknown", "message": "no such job"}))

    async def v2(request: Request) -> Response:
        return JSONResponse({"detail": V2_UNSUPPORTED}, status_code=501)

    app = Starlette(routes=[
        Route("/configure", configure, methods=["GET"]),
        Route("/setup", setup, methods=["POST"]),
        Route("/evaluate", evaluate, methods=["POST"]),
        Route("/evaluate_async", evaluate_async, methods=["POST"]),
        Route("/evaluate_result", evaluate_result, methods=["GET"]),
        Route("/reset", v2, methods=["POST"]),
        Route("/initialize_task", v2, methods=["POST"]),
    ])
    app.add_middleware(_Bearer, token=token.strip())
    return app
