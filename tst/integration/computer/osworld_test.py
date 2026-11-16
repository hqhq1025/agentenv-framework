"""OSWorld on a computer env, end to end, from public parts alone: the osworld bundle's desk, the ubuntu-24.04 base with
the reference driver and the shipped osworld-server app, which runs upstream OSWorld's guest server, deployed; the
shipped osworld-controller, built from upstream OSWorld, deployed beside it by osworld_deploy_controller as the default
for a step that names no image, in a container of its own that reaches the guest server only through the endpoint the
computer's card advertises, with the env's control token; osworld_initialize running a classic init_config's execute on
the computer through it; and osworld_evaluate grading what that wrote by upstream's own checks: check_include_exclude
over a vm_command_line getter, 1.0 for a rule it meets and 0.0 for one it doesn't, and file_contains over a vm_file
getter, the file fetched from the computer through both forwarders."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_env.cli import cli
from tst.integration.computer.conftest import run_context

pytestmark = [pytest.mark.integration, pytest.mark.int_test_slow]

OSWORLD = Path(__file__).parent / "osworld"


def test_a_classic_task_is_set_up_and_graded_on_a_computer_through_the_public_controller_beside_it(state):
    ran = CliRunner().invoke(cli, ["run", str(OSWORLD), "--task", "classic", "--no-emulate"])

    assert ran.exit_code == 0, ran.output
    context = run_context(ran.output)
    verifications = context["metadata"]["verifications"]
    assert verifications["pass"]["score"] == 1.0, verifications["pass"]
    assert verifications["fail"]["score"] == 0.0, verifications["fail"]
    assert verifications["file"]["score"] == 1.0, verifications["file"]
    (deployed,) = context["deployed_envs"]
    recorded = context["metadata"]["osworld_controllers"][deployed["env_id"]]
    assert recorded["osworld_guest_url"].endswith("/svc/osworld-server/osworld")
    assert recorded["osworld_controller_sandbox_id"] in {s["sandbox_id"] for s in context["deployed_sandboxes"]}
