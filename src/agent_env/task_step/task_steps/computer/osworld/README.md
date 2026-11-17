OSWorld tasks on agent-env computers, from upstream OSWorld ([xlang-ai/OSWorld](https://github.com/xlang-ai/OSWorld),
Apache-2.0) at commit `5123bfdbbc34b947bcffcff564abc62d0ab7072d` (`OSWORLD_COMMIT`). The `osworld_deploy_controller`,
`osworld_initialize` and `osworld_evaluate` steps set a task up and grade it through a controller deployed beside the
computer, out of the agent's reach; this bundle ships the two pieces they run on. It has no tasks.

`envs/osworld-server/` is the `osworld-server` computer app, for an X11 desktop on Linux (`amd64` and `arm64`), such as
`ubuntu-24.04`: upstream's guest server (`desktop_env/server/main.py`, with `pyxcursor.py` and `screen_recording.py`),
downloaded when the computer installs the app and checked by sha256, run unmodified as the session user on
127.0.0.1:5000, without Werkzeug's debugger. The app's own server forwards `/osworld/<route>` to it, and its card
advertises that prefix as the endpoint of the `urn:agentenv:osworld-guest/v1` extension, which the computer serves
behind the env's control token. Name it in a computer env's `apps` as `osworld-server`, its alias, or by its id,
`@local/agentenv-framework/osworld/osworld-server`.

`artifacts/osworld-controller/` is the controller image `osworld_deploy_controller` deploys when a step names no
`controller_image_artifact_id`: upstream's `SetupController`, getters and metrics at the same commit
(`fetch-osworld.sh`, from GitHub's archive of it, checked by sha256), with its dependencies locked by hash
(`requirements.txt`, from `requirements.in`). Name it as `osworld-controller`, or by its id,
`@local/agentenv-framework/osworld/osworld-controller`. Upstream's code runs unmodified: a forwarder on its loopback
port 5000 sends each of its calls on to the guest server with the computer's headers.

## The controller's contract

Any controller image the step deploys is started with this env and serves these routes on port 8000.

| Env | |
| --- | --- |
| `CONTROLLER_AUTH_TOKEN` | The bearer token every call to the controller must carry; without it a call is 401. The step makes it at random for each deploy, never from the env's control token, which the computer's session can read; it keeps it in the run's live context, never stored, and refuses a controller that answers without it. A run resumed from its stored context has no token, so `osworld_deploy_controller` run again replaces the recorded controller with a new one. |
| `REMOTE_VM_IP` | The guest server's base URL, path prefix and all: the computer's `/osworld` endpoint of the app. |
| `REMOTE_SERVER_HEADERS` | A JSON object of the headers every call to the guest server carries: the computer's control token. |
| `PROVIDER` | `remote`: the controller drives a guest server it reaches over HTTP. |
| `OS_TYPE` | The guest's OS type, `Ubuntu`. |
| `LITELLM_API_KEY`, `LITELLM_BASE_URL` | The judge's model endpoint, for an image that serves LLM-judge funcs; this image ignores them. |

| Route | |
| --- | --- |
| `GET /configure` | 200 once the controller is up. |
| `POST /setup` | `{"config": [{"type", "parameters"}, ...]}`, a task's `init_config` or an evaluator's `postconfig`, run by upstream's `SetupController`: `{"success": true, "steps": n}`, or `{"success": false, "error"}`. |
| `POST /evaluate` | One func: `{"func", "result", "expected", "options"}`, as upstream's `DesktopEnv.evaluate` reads them: `{"score", "error", "message"}`, the score 0 with why when a getter or the metric fails. A func or getter upstream doesn't have is 422. |
| `POST /evaluate_async`, `GET /evaluate_result?job_id=` | The same check started, then polled: `{"job_id"}`, then `{"status": "running"}`, `{"status": "done", "result"}` or `{"status": "error", "message"}`. |
| `POST /reset`, `POST /initialize_task` | An OSWorld-V2 task's setup, for an image that serves it. |

This image answers 501 to an LLM-judge func (`agent_judge` and its variants) and to an OSWorld-V2 task, which need an
image that serves them, named by `controller_image_artifact_id`. A setup or check that needs the computer's Chrome
DevTools port (9222) or VLC's HTTP port (8080), which the app doesn't expose, fails saying so. Google Drive setups,
upstream's proxy, and `compare_image_text`, whose easyocr the image leaves out, don't work either.
