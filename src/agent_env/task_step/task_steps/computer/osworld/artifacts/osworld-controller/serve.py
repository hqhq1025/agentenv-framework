"""``python serve.py``: the controller, from its env (``server.CONTROLLER_ENV_KEYS``), on port 8000, with the forwarder to
the guest server on 127.0.0.1:5000 and the refusers on the guest's other loopback ports (:mod:`forwarder`), all in one
event loop. It refuses to start without a token or a guest server, or for a provider other than ``remote``."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

import httpx
import uvicorn

from forwarder import GUEST_HOST, GUEST_PORT, REFUSED_PORTS, Refusals, create_forwarder, create_refuser
from server import (GUEST_HEADERS_ENV, GUEST_URL_ENV, OS_TYPE_ENV, PROVIDER_ENV, REMOTE_PROVIDER, TOKEN_ENV,
                    create_app)

PORT = 8000
#: The upstream commit the image was built from (the Dockerfile's ``OSWORLD_COMMIT``).
OSWORLD_COMMIT = os.environ.get("OSWORLD_COMMIT", "")


def settings(env: dict[str, str]) -> dict:
    """The controller's settings from ``env``; raises ValueError naming what's missing or wrong."""
    if (provider := env.get(PROVIDER_ENV, REMOTE_PROVIDER)) != REMOTE_PROVIDER:
        raise ValueError(f"{PROVIDER_ENV}={provider!r}: this controller drives a guest server it reaches over HTTP, "
                         f"{PROVIDER_ENV}={REMOTE_PROVIDER!r}")
    if not (token := env.get(TOKEN_ENV, "").strip()):
        raise ValueError(f"{TOKEN_ENV} is unset; the controller refuses to serve without a token")
    if not (guest_url := env.get(GUEST_URL_ENV, "").strip()):
        raise ValueError(f"{GUEST_URL_ENV} is unset: the guest server's base URL")
    try:
        headers = json.loads(env.get(GUEST_HEADERS_ENV) or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"{GUEST_HEADERS_ENV} isn't JSON: {e}") from e
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise ValueError(f"{GUEST_HEADERS_ENV} must be a JSON object of header names to values")
    return {"token": token, "guest_url": guest_url, "headers": headers, "os_type": env.get(OS_TYPE_ENV) or "Ubuntu",
            "client_password": env.get("CLIENT_PASSWORD", "")}


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = settings(dict(os.environ))
    except ValueError as e:
        sys.exit(f"osworld-controller: {e}")
    from runtime import OSWorldRuntime  # upstream OSWorld, imported once the env is known to be good

    refusals = Refusals()
    runtime = OSWorldRuntime(refusals=refusals, os_type=config["os_type"], client_password=config["client_password"])
    app = create_app(config["token"], runtime, os_type=config["os_type"], osworld_commit=OSWORLD_COMMIT)
    async with httpx.AsyncClient() as client:
        servers = [uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")),
                   uvicorn.Server(uvicorn.Config(create_forwarder(config["guest_url"], config["headers"], client),
                                                 host=GUEST_HOST, port=GUEST_PORT, log_level="warning")),
                   *(uvicorn.Server(uvicorn.Config(create_refuser(port, refusals), host=GUEST_HOST, port=port,
                                                   log_level="warning")) for port in REFUSED_PORTS)]
        await asyncio.gather(*(server.serve() for server in servers))


if __name__ == "__main__":
    asyncio.run(main())
