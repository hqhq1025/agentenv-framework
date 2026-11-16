"""Upstream OSWorld's setup and checks, run unmodified against the guest server through the forwarder (:mod:`forwarder`):
``SetupController.setup`` for a task's config, and for one func, the single-metric branch of ``DesktopEnv.evaluate``,
whose result and expected getters take the env this module stands in for (``GuestEnv``), with exactly the attributes
upstream's getters read. ``DesktopEnv`` itself isn't imported: it builds a VM provider and needs gymnasium.

The only module of the controller that imports upstream OSWorld, from ``/opt/osworld`` (``fetch-osworld.sh``)."""
from __future__ import annotations

import logging
import os
from typing import Any

import desktop_env.controllers.setup as upstream_setup
from desktop_env.controllers.python import PythonController
from desktop_env.controllers.setup import SetupController
from desktop_env.evaluators import getters, metrics

from forwarder import GUEST_HOST, GUEST_PORT, Refusals, needed
from server import UnknownCheck

logger = logging.getLogger("osworld_controller")

#: The ports upstream reaches Chrome's DevTools and VLC on, which the forwarder refuses (``forwarder.REFUSED_PORTS``).
CHROMIUM_PORT, VLC_PORT = 9222, 8080
#: How often upstream's setup polls the guest server's /terminal before giving up, 5 s apart; its own 20 holds a call
#: on a dead guest for minutes past what a step waits for.
SETUP_RETRIES = 3
#: The screen a task's ``{SCREEN_WIDTH}``-style placeholders read when the guest won't say its own.
DEFAULT_SCREEN = (1920, 1080)


class GuestEnv:
    """What upstream's getters read of a ``DesktopEnv``: the guest's address and ports, a cache folder, its controllers
    and, through them, its platform."""

    def __init__(self, *, cache_dir: str, os_type: str, client_password: str):
        self.vm_ip, self.server_port = GUEST_HOST, GUEST_PORT
        self.chromium_port, self.vlc_port = CHROMIUM_PORT, VLC_PORT
        self.cache_dir = cache_dir
        self.os_type = os_type
        self.client_password = client_password
        self.current_use_proxy = False
        self.is_environment_used = True
        self.controller = PythonController(vm_ip=self.vm_ip, server_port=self.server_port)
        self.setup_controller: SetupController | None = None

    @property
    def vm_platform(self) -> str:
        return self.controller.get_vm_platform()

    @property
    def vm_machine(self) -> str:
        return self.controller.get_vm_machine()


class OSWorldRuntime:
    """Setup and checks on the guest, upstream's, saying which unexposed port a failure needed (``Refusals``)."""

    def __init__(self, *, refusals: Refusals, cache_dir: str = "/tmp/osworld-cache", os_type: str = "Ubuntu",
                 client_password: str = ""):
        os.makedirs(cache_dir, exist_ok=True)
        upstream_setup.MAX_RETRIES = SETUP_RETRIES
        self.refusals = refusals
        self.env = GuestEnv(cache_dir=cache_dir, os_type=os_type, client_password=client_password)

    def _setup_controller(self) -> SetupController:
        if self.env.setup_controller is None:
            try:
                size = self.env.controller.get_vm_screen_size() or {}
                width, height = int(size["width"]), int(size["height"])
            except Exception as e:
                logger.warning(f"the guest's screen size is unknown ({type(e).__name__}); using {DEFAULT_SCREEN}")
                width, height = DEFAULT_SCREEN
            self.env.setup_controller = SetupController(
                vm_ip=self.env.vm_ip, server_port=self.env.server_port, chromium_port=self.env.chromium_port,
                vlc_port=self.env.vlc_port, cache_dir=self.env.cache_dir, client_password=self.env.client_password,
                screen_width=width, screen_height=height, os_type=self.env.os_type)
        return self.env.setup_controller

    def _explained(self, error: Exception, mark: int) -> Exception:
        """``error``, with the unexposed ports it needed when it called any."""
        if ports := self.refusals.since(mark):
            return RuntimeError(f"{type(error).__name__}: {error}; {needed(ports)}")
        return error

    def setup(self, config: list[dict]) -> None:
        mark = self.refusals.mark_count()
        try:
            if self._setup_controller().setup(config) is False:
                raise RuntimeError(f"the guest server didn't answer /terminal after {SETUP_RETRIES} tries")
        except Exception as e:
            raise self._explained(e, mark) from e

    def evaluate(self, body: dict) -> float:
        metric = _named(metrics, body["func"], "metric")
        result_config, expected_config = body.get("result"), body.get("expected")
        if not isinstance(result_config, dict):
            raise ValueError(f"{body['func']}: the evaluator gives no result getter")
        result_getter = _getter(result_config)
        expected_getter = _getter(expected_config) if isinstance(expected_config, dict) and expected_config else None
        options: dict[str, Any] = body.get("options") or {}
        mark = self.refusals.mark_count()
        try:
            try:
                result = result_getter(self.env, result_config)
            except FileNotFoundError:
                logger.warning(f"{body['func']}: the result file wasn't found; it scores 0")
                return 0.0
            if expected_getter is not None:
                return float(metric(result, expected_getter(self.env, expected_config), **options))
            return float(metric(result, **options))
        except Exception as e:
            raise self._explained(e, mark) from e


def _named(module: Any, name: str, what: str) -> Any:
    found = getattr(module, name, None) if isinstance(name, str) and not name.startswith("_") else None
    if not callable(found):
        raise UnknownCheck(f"upstream OSWorld has no {what} {name!r}")
    return found


def _getter(config: dict) -> Any:
    return _named(getters, f"get_{config.get('type')}", "getter")
