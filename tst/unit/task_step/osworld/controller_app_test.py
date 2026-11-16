"""The osworld bundle's controller image, its files loaded by path: every route refuses a call without the bearer token,
and the steps' own client waits for it, checks it requires the token, sets up and grades through it; setup and checks
answer in the shapes the steps read, an LLM-judge func and an OSWorld-V2 task are 501 and an unknown check 422; the
forwarder sends upstream's calls on to the guest server's base URL with its headers, and the refusers answer for the
ports the app doesn't expose, which a failed call then names; upstream's setup and checks run with the env upstream's
getters read, on stand-ins for upstream; and the env the step starts it with is the env it reads."""
from __future__ import annotations

import json
import sys
import time
import types

import httpx
import pytest
from starlette.testclient import TestClient

from agent_env.task_step.task_steps.computer import osworld
from agent_env.task_step.task_steps.computer.osworld import controller
from agent_env.task_step.task_steps.computer.osworld import deploy_controller as deploy_module
from tst.unit.task_step.osworld.helpers import card, computer

TOKEN = "c0ntr0ller"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
_MODULES = ("server", "forwarder", "runtime", "serve")


@pytest.fixture
def ctl():
    """The controller's modules, imported from its folder as the image runs them."""
    saved = {name: sys.modules.pop(name) for name in _MODULES if name in sys.modules}
    sys.path.insert(0, str(osworld.CONTROLLER_DIR))
    try:
        import forwarder
        import serve
        import server
        yield types.SimpleNamespace(server=server, forwarder=forwarder, serve=serve)
    finally:
        sys.path.remove(str(osworld.CONTROLLER_DIR))
        for name in (*_MODULES, *[m for m in sys.modules if m == "desktop_env" or m.startswith("desktop_env.")]):
            sys.modules.pop(name, None)
        sys.modules.update(saved)


class FakeRuntime:
    def __init__(self, score=1.0, error=None, setup_error=None, unknown=False):
        self.score, self.error, self.setup_error, self.unknown = score, error, setup_error, unknown
        self.setups, self.bodies = [], []

    def setup(self, config):
        self.setups.append(config)
        if self.setup_error:
            raise self.setup_error

    def evaluate(self, body):
        self.bodies.append(body)
        if self.unknown:
            from server import UnknownCheck
            raise UnknownCheck(f"upstream OSWorld has no metric {body['func']!r}")
        if self.error:
            raise self.error
        return self.score


def _client(ctl, runtime=None) -> TestClient:
    return TestClient(ctl.server.create_app(TOKEN, runtime or FakeRuntime(), osworld_commit=osworld.OSWORLD_COMMIT))


_ROUTES = [("GET", "/configure"), ("POST", "/setup"), ("POST", "/evaluate"), ("POST", "/evaluate_async"),
           ("GET", "/evaluate_result"), ("POST", "/reset"), ("POST", "/initialize_task")]


@pytest.mark.parametrize("method,path", _ROUTES)
@pytest.mark.parametrize("header", [None, "Bearer wrong", f"Basic {TOKEN}", TOKEN, "Bearer "])
def test_every_route_refuses_a_call_without_the_token(ctl, method, path, header):
    runtime = FakeRuntime()
    response = _client(ctl, runtime).request(method, path, json={"func": "f", "config": []},
                                             headers={"Authorization": header} if header else {})
    assert response.status_code == 401 and TOKEN not in response.text
    assert runtime.setups == [] and runtime.bodies == []


@pytest.mark.parametrize("token", ["", "  "])
def test_the_controller_refuses_to_serve_without_a_token(ctl, token):
    with pytest.raises(ValueError, match="CONTROLLER_AUTH_TOKEN"):
        ctl.server.create_app(token, FakeRuntime())


def test_configure_answers_with_the_token_and_says_what_it_serves(ctl):
    response = _client(ctl).get("/configure", headers={"Authorization": f"bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.json() == {"os_type": "Ubuntu", "osworld_commit": osworld.OSWORLD_COMMIT,
                               "serves": ["setup", "evaluate"]}


def test_setup_runs_the_config_and_answers_whether_it_did(ctl):
    runtime = FakeRuntime()
    config = [{"type": "execute", "parameters": {"command": ["true"]}}]
    assert _client(ctl, runtime).post("/setup", json={"config": config}, headers=AUTH).json() == {
        "success": True, "steps": 1}
    assert runtime.setups == [config]
    failing = FakeRuntime(setup_error=RuntimeError("Setup step 1 failed: " + "x" * 5000))
    answer = _client(ctl, failing).post("/setup", json={"config": config}, headers=AUTH).json()
    assert answer["success"] is False and answer["error"].startswith("RuntimeError: Setup step 1 failed")
    assert len(answer["error"]) == ctl.server.ERROR_CHARS
    assert _client(ctl).post("/setup", json={"config": {"type": "x"}}, headers=AUTH).status_code == 400


def test_evaluate_answers_a_score_or_a_zero_with_why(ctl):
    body = {"func": "check_include_exclude", "result": {"type": "vm_command_line"}, "options": {}}
    runtime = FakeRuntime(score=0.5)
    assert _client(ctl, runtime).post("/evaluate", json=body, headers=AUTH).json() == {
        "score": 0.5, "error": False, "message": ""}
    assert runtime.bodies == [body]
    answer = _client(ctl, FakeRuntime(error=KeyError("path"))).post("/evaluate", json=body, headers=AUTH).json()
    assert answer == {"score": 0.0, "error": True, "message": "KeyError: 'path'"}


def test_a_check_upstream_doesnt_have_is_a_422(ctl):
    response = _client(ctl, FakeRuntime(unknown=True)).post("/evaluate", json={"func": "no_such"}, headers=AUTH)
    assert response.status_code == 422 and "no metric 'no_such'" in response.json()["detail"]
    assert _client(ctl).post("/evaluate", json={"result": {}}, headers=AUTH).status_code == 400


@pytest.mark.parametrize("path", ["/evaluate", "/evaluate_async"])
@pytest.mark.parametrize("func", ["agent_judge", "agent_judge_zip", "agent_judge_multi"])
def test_an_llm_judge_func_is_a_501_for_an_image_that_serves_judges(ctl, path, func):
    runtime = FakeRuntime()
    response = _client(ctl, runtime).post(path, json={"func": func, "rubric": [{"criterion": "x"}]}, headers=AUTH)
    assert response.status_code == 501 and response.json()["detail"] == ctl.server.JUDGE_UNSUPPORTED
    assert "controller_image_artifact_id" in ctl.server.JUDGE_UNSUPPORTED and runtime.bodies == []


@pytest.mark.parametrize("path,body", [("/reset", {}), ("/initialize_task", {"osworld_v2_task_url": "https://t.py"}),
                                       ("/evaluate", {"func": "osworld_v2", "osworld_v2_task_url": "https://t.py"})])
def test_an_osworld_v2_task_is_a_501_for_an_image_that_serves_it(ctl, path, body):
    response = _client(ctl).post(path, json=body, headers=AUTH)
    assert response.status_code == 501 and response.json()["detail"] == ctl.server.V2_UNSUPPORTED


def test_an_async_check_runs_to_its_result_and_an_unknown_job_says_so(ctl):
    client = _client(ctl, FakeRuntime(score=1))
    job_id = client.post("/evaluate_async", json={"func": "exact_match"}, headers=AUTH).json()["job_id"]
    for _ in range(100):
        status = client.get("/evaluate_result", params={"job_id": job_id}, headers=AUTH).json()
        if status["status"] != "running":
            break
        time.sleep(0.01)
    assert status == {"status": "done", "result": {"score": 1.0, "error": False, "message": ""}}
    assert client.get("/evaluate_result", params={"job_id": "nope"}, headers=AUTH).json()["status"] == "unknown"
    unknown = _client(ctl, FakeRuntime(unknown=True))
    job_id = unknown.post("/evaluate_async", json={"func": "no_such"}, headers=AUTH).json()["job_id"]
    for _ in range(100):
        status = unknown.get("/evaluate_result", params={"job_id": job_id}, headers=AUTH).json()
        if status["status"] != "running":
            break
        time.sleep(0.01)
    assert status["status"] == "error" and "no metric" in status["message"]


@pytest.fixture
def served(ctl, monkeypatch):
    """The controller, which the steps' client reaches in process, and its URL."""
    runtime = FakeRuntime(score=1.0)
    transport = httpx.ASGITransport(app=ctl.server.create_app(TOKEN, runtime))
    real = httpx.AsyncClient
    monkeypatch.setattr(controller.httpx, "AsyncClient", lambda *args, **kwargs: real(transport=transport))
    monkeypatch.setattr(controller, "_POLL_INTERVAL_S", 0.01)
    return "http://controller", runtime


@pytest.mark.asyncio
async def test_the_steps_client_waits_for_it_finds_it_requires_the_token_and_sets_up_and_grades_through_it(served):
    url, runtime = served
    endpoint = controller.Endpoint(url, controller.bearer_headers(TOKEN))
    await controller.wait_ready(endpoint, timeout=10, interval=0.1)
    await controller.require_auth(endpoint)
    answer = await controller.post(endpoint, "/setup", {"config": [{"type": "sleep", "parameters": {"seconds": 0}}]})
    assert answer["success"] is True
    assert (await controller.evaluate(endpoint, {"func": "exact_match", "result": {"type": "rule"}}))["score"] == 1.0
    assert runtime.bodies == [{"func": "exact_match", "result": {"type": "rule"}}]


def _forwarding(ctl, upstream, base="https://env.example/svc/osworld-server/osworld/") -> TestClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    return TestClient(ctl.forwarder.create_forwarder(base, {"X-Control": "control"}, client))


def test_the_forwarder_sends_upstreams_calls_to_the_guest_servers_base_url_with_its_headers(ctl):
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, content=b"file bytes", headers={"x-guest": "1", "connection": "close"})

    response = _forwarding(ctl, upstream).post("/file?a=1&a=2", data={"file_path": "/tmp/x"},
                                               headers={"Proxy-Authorization": "hop", "X-Kept": "yes"})
    assert response.status_code == 200 and response.content == b"file bytes" and response.headers["x-guest"] == "1"
    [request] = seen
    assert str(request.url) == "https://env.example/svc/osworld-server/osworld/file?a=1&a=2"
    assert request.method == "POST" and request.content == b"file_path=%2Ftmp%2Fx"
    assert request.headers["x-control"] == "control" and request.headers["x-kept"] == "yes"
    assert request.headers["host"] == "env.example" and "proxy-authorization" not in request.headers
    assert "connection" not in response.headers


def test_the_forwarder_answers_502_when_the_guest_server_is_unreachable(ctl):
    def upstream(request):
        raise httpx.ConnectError("refused")

    response = _forwarding(ctl, upstream).get("/screen_size")
    assert response.status_code == 502 and "unreachable" in response.text


@pytest.mark.parametrize("port,named", [(9222, "Chrome DevTools port 9222"), (8080, "VLC's HTTP port 8080")])
def test_a_refuser_answers_501_and_records_the_port_it_was_called_on(ctl, port, named):
    refusals = ctl.forwarder.Refusals()
    mark = refusals.mark_count()
    client = TestClient(ctl.forwarder.create_refuser(port, refusals))
    response = client.get("/json/version")
    assert response.status_code == 501 and named in response.text and "public controller" in response.text
    with pytest.raises(Exception):
        with client.websocket_connect("/devtools/browser/x") as socket_:
            socket_.receive_text()
    assert refusals.since(mark) == [port]
    assert refusals.since(refusals.mark_count()) == []


# Upstream OSWorld, stood in for: its setup controller, its Python controller, and a getter and metric module.

class FakeSetupController:
    made = []

    def __init__(self, **kwargs):
        self.kwargs, self.configs = kwargs, []
        FakeSetupController.made.append(self)

    def setup(self, config, use_proxy=False):
        self.configs.append(config)
        return not any(step.get("type") == "unreachable" for step in config)


class FakePythonController:
    def __init__(self, vm_ip, server_port):
        self.vm_ip, self.server_port = vm_ip, server_port

    def get_vm_screen_size(self):
        return {"width": 1280, "height": 800}

    def get_vm_platform(self):
        return "Linux"

    def get_vm_machine(self):
        return "x86_64"


def _upstream(monkeypatch, refusals=None):
    """Stand-ins for upstream's modules, whose getter calls the refused CDP port when its config says to."""
    modules = {name: types.ModuleType(name) for name in (
        "desktop_env", "desktop_env.controllers", "desktop_env.controllers.setup", "desktop_env.controllers.python",
        "desktop_env.evaluators", "desktop_env.evaluators.getters", "desktop_env.evaluators.metrics")}
    modules["desktop_env.controllers.setup"].SetupController = FakeSetupController
    modules["desktop_env.controllers.setup"].MAX_RETRIES = 20
    modules["desktop_env.controllers.python"].PythonController = FakePythonController
    getters, metrics = modules["desktop_env.evaluators.getters"], modules["desktop_env.evaluators.metrics"]
    seen = []

    def get_vm_command_line(env, config):
        seen.append((env, config))
        if config.get("missing"):
            raise FileNotFoundError(config["missing"])
        if config.get("cdp"):
            refusals.mark(9222)
            raise ConnectionError("CDP refused")
        return "osworld-e2e"

    getters.get_vm_command_line = get_vm_command_line
    getters.get_rule = lambda env, config: config["rules"]
    metrics.check_include_exclude = lambda result, rules, **options: float(all(t in result for t in rules["include"]))
    metrics.exact_match = lambda result, **options: float(result == options.get("value"))
    modules["desktop_env.evaluators"].getters, modules["desktop_env.evaluators"].metrics = getters, metrics
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return modules, seen


def _runtime(ctl, monkeypatch, tmp_path):
    refusals = ctl.forwarder.Refusals()
    modules, seen = _upstream(monkeypatch, refusals)
    import runtime
    FakeSetupController.made.clear()
    return runtime, runtime.OSWorldRuntime(refusals=refusals, cache_dir=str(tmp_path / "cache"),
                                           client_password="pw"), modules, seen


def test_checks_run_upstreams_way_with_the_env_its_getters_read(ctl, monkeypatch, tmp_path):
    runtime, rt, modules, seen = _runtime(ctl, monkeypatch, tmp_path)
    rule = {"type": "rule", "rules": {"include": ["osworld-e2e"]}}
    assert rt.evaluate({"func": "check_include_exclude", "result": {"type": "vm_command_line"}, "expected": rule}) == 1.0
    assert rt.evaluate({"func": "exact_match", "result": {"type": "vm_command_line"},
                        "options": {"value": "osworld-e2e"}}) == 1.0
    assert rt.evaluate({"func": "exact_match", "result": {"type": "vm_command_line", "missing": "/x"}}) == 0.0
    env = seen[0][0]
    assert (env.vm_ip, env.server_port, env.chromium_port, env.vlc_port) == ("127.0.0.1", 5000, 9222, 8080)
    assert env.cache_dir == str(tmp_path / "cache") and (tmp_path / "cache").is_dir()
    assert (env.vm_platform, env.vm_machine, env.current_use_proxy, env.is_environment_used) == (
        "Linux", "x86_64", False, True)
    assert env.controller.vm_ip == "127.0.0.1" and env.os_type == "Ubuntu"


@pytest.mark.parametrize("body,named", [({"func": "no_metric", "result": {"type": "vm_command_line"}}, "metric"),
                                        ({"func": "_private", "result": {"type": "vm_command_line"}}, "metric"),
                                        ({"func": "exact_match", "result": {"type": "no_getter"}}, "getter")])
def test_a_check_upstream_doesnt_have_is_unknown(ctl, monkeypatch, tmp_path, body, named):
    _, rt, _, _ = _runtime(ctl, monkeypatch, tmp_path)
    with pytest.raises(ctl.server.UnknownCheck, match=f"no {named}"):
        rt.evaluate(body)


def test_a_check_that_needed_an_unexposed_port_says_so(ctl, monkeypatch, tmp_path):
    _, rt, _, _ = _runtime(ctl, monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="Chrome DevTools port 9222.*not supported by the public controller"):
        rt.evaluate({"func": "exact_match", "result": {"type": "vm_command_line", "cdp": True}})
    with pytest.raises(ValueError, match="no result getter"):
        rt.evaluate({"func": "exact_match"})


def test_setup_runs_upstreams_setup_controller_sized_to_the_guests_screen(ctl, monkeypatch, tmp_path):
    runtime, rt, modules, _ = _runtime(ctl, monkeypatch, tmp_path)
    assert modules["desktop_env.controllers.setup"].MAX_RETRIES == runtime.SETUP_RETRIES
    rt.setup([{"type": "execute", "parameters": {"command": ["true"]}}])
    rt.setup([])
    (made,) = FakeSetupController.made
    assert made.kwargs == {"vm_ip": "127.0.0.1", "server_port": 5000, "chromium_port": 9222, "vlc_port": 8080,
                           "cache_dir": str(tmp_path / "cache"), "client_password": "pw", "screen_width": 1280,
                           "screen_height": 800, "os_type": "Ubuntu"}
    with pytest.raises(RuntimeError, match="didn't answer /terminal"):
        rt.setup([{"type": "unreachable"}])


def test_the_env_the_step_starts_it_with_is_the_env_it_reads(ctl):
    assert ctl.server.CONTROLLER_ENV_KEYS == controller.CONTROLLER_ENV_KEYS
    assert ctl.server.REMOTE_PROVIDER == controller.REMOTE_PROVIDER
    deployed = computer(card("/apps/7/osworld"))
    env = deploy_module.OSWorldDeployControllerTaskStep(id="c", version=1, env_id="desk").controller_env(
        deployed, "https://env.example/apps/7/osworld")
    config = ctl.serve.settings(env)
    assert config["token"] == env[controller.TOKEN_ENV] and config["guest_url"] == "https://env.example/apps/7/osworld"
    assert config["headers"] == deployed.control_headers and config["os_type"] == "Ubuntu"


@pytest.mark.parametrize("env,problem", [
    ({"PROVIDER": "docker", "CONTROLLER_AUTH_TOKEN": "t", "REMOTE_VM_IP": "u"}, "PROVIDER='docker'"),
    ({"REMOTE_VM_IP": "u"}, "CONTROLLER_AUTH_TOKEN is unset"),
    ({"CONTROLLER_AUTH_TOKEN": "t"}, "REMOTE_VM_IP is unset"),
    ({"CONTROLLER_AUTH_TOKEN": "t", "REMOTE_VM_IP": "u", "REMOTE_SERVER_HEADERS": "{"}, "isn't JSON"),
    ({"CONTROLLER_AUTH_TOKEN": "t", "REMOTE_VM_IP": "u", "REMOTE_SERVER_HEADERS": json.dumps({"a": 1})},
     "JSON object of header names"),
])
def test_the_controller_refuses_to_start_on_a_bad_env(ctl, env, problem):
    with pytest.raises(ValueError, match=problem):
        ctl.serve.settings(env)


def test_the_step_deploys_the_shipped_controller_by_default_with_the_contracts_token_env():
    assert deploy_module.CONTROLLER_TOKEN_ENV == controller.TOKEN_ENV
    assert deploy_module.DEFAULT_CONTROLLER_IMAGE_ARTIFACT_ID == "@local/agentenv-framework/osworld/osworld-controller"
