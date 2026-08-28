# Jetson Thor Monitor

Real-time monitoring dashboard and AI workspace for NVIDIA Jetson Thor.

Features include system telemetry, Qwen chat, Markdown rendering, image generation/editing, web-enabled agent tools, and an isolated Python Code Interpreter.

## Run locally

```bash
python3 server.py
```

The default address is `http://localhost:8090`. Set `THOR_MONITOR_PORT` to use another port.

## Service configuration

Create the service environment file from the tracked example and restrict its permissions:

```bash
cp thor-monitor.env.example thor-monitor.env
chmod 600 thor-monitor.env
```

Edit `thor-monitor.env` before installing the service. The included `thor-monitor.service` reads this user-owned file from `/home/juper007/thor-monitor/thor-monitor.env`:

```ini
THOR_MONITOR_PASSWORD=replace-with-a-strong-password
THOR_IMAGE_API_KEY=replace-with-the-image-api-key
THOR_AI_CONCURRENCY=1
```

Install or update the unit only after the environment file exists:

```bash
sudo cp thor-monitor.service /etc/systemd/system/thor-monitor.service
sudo systemctl daemon-reload
sudo systemctl restart thor-monitor.service
```

Copy `qwen-image/qwen-image.env.example` to `qwen-image/qwen-image.env` and replace the placeholder before starting the image service. Environment files and generated model artifacts are intentionally excluded from Git.

## Tests

Run the regression suite from the project root:

```bash
python3 -m unittest discover -s tests -v
```

The suite covers tool-call parsing, agent tool execution, authentication, request limits, path traversal protection, image proxy history, and the AI Workspace UI contract. The real Code Interpreter integration test is skipped automatically when Docker or its prebuilt sandbox image is unavailable.

## Agent run API

`POST /api/chat` accepts an optional `run_id` containing 1–64 letters, numbers, underscores, or hyphens. The final NDJSON object includes that ID and `run_state`. Recent in-memory runs can be inspected with `GET /api/chat/runs/{run_id}` and cancelled with `POST /api/chat/cancel` using `{"run_id":"..."}`. Run state is currently memory-only; persistent recovery is planned for Stage 4.

Run the optional live web-tool smoke test on a networked host:

```bash
python3 scripts/smoke_web_tool.py
```

## Architecture

The server entrypoint is intentionally thin around the agent subsystem:

```text
server.py          HTTP routes, telemetry, image proxy
agent/             model client, agent runtime, run states
tools/             ToolSpec, ToolRegistry, schemas, built-in implementations
agent_tools.py     compatibility facade and Qwen tool-call parser
sandbox/           execution isolation package
storage/           persistent storage package
skills/            reusable agent guidance
tests/             regression and architecture contracts
```

`agent_tools.py` remains a compatibility entrypoint while `tools/registry.py` owns registration, JSON Schema validation, standardized error codes, risk metadata, timeouts, timing, and incremental output limits. Registered schemas are immutable copies. `AgentRuntime` receives the Registry and parsing dependencies through its constructor rather than importing the compatibility facade. It tracks bounded recent runs through explicit states and structured events, with cancellation and iteration, tool-call, and wall-clock limits.
