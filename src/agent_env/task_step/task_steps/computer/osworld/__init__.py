"""OSWorld tasks on agent-env computers, and the ``osworld`` bundle agentenv-framework ships.

``osworld_deploy_controller`` deploys the OSWorld controller beside a computer that runs the ``osworld-server`` app,
``osworld_initialize`` sets a task up through it and ``osworld_evaluate`` grades it there. Beside the steps live what
they share: how a step finds the guest server (:mod:`.guest`) and how it calls the controller (:mod:`.controller`).

This folder is also the ``osworld`` bundle (``README.md``), whose entities a task names by their aliases
(``aliases.toml``): the ``osworld-server`` computer app (``envs/``), which runs upstream OSWorld's guest server, fetched
at ``OSWORLD_COMMIT`` and checked by sha256, on a computer; and the ``osworld-controller`` image (``artifacts/``), a
controller built from upstream OSWorld's setup, getters and metrics at that commit, which ``osworld_deploy_controller``
deploys when a step names no image of its own."""
from __future__ import annotations

from pathlib import Path

#: The upstream OSWorld commit the guest server and the controller image are built from (xlang-ai/OSWorld, Apache-2.0).
OSWORLD_COMMIT = "5123bfdbbc34b947bcffcff564abc62d0ab7072d"
#: The app's ``environment_name``.
APP_NAME = "osworld-server"
#: The extension the app's card advertises at the prefix its server forwards to the guest server under. A computer
#: nests the app's card under its ``children_environments`` with the endpoint rooted where it serves the app, so a step
#: finds the guest server by this URI in the computer's card.
GUEST_EXTENSION = "urn:agentenv:osworld-guest/v1"
#: The prefix the app's server forwards to the guest server under.
PROXY_PREFIX = "/osworld"
#: The bundle's folder, this package's.
BUNDLE_DIR = Path(__file__).parent
#: The app's folder.
APP_DIR = BUNDLE_DIR / "envs" / APP_NAME
#: The controller image's folder, its build context.
CONTROLLER_DIR = BUNDLE_DIR / "artifacts" / "osworld-controller"
