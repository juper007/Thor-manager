# Jetson Thor Monitor

Real-time monitoring dashboard and AI workspace for NVIDIA Jetson Thor.

Features include system telemetry, Qwen chat, Markdown rendering, image generation/editing, web-enabled agent tools, and an isolated Python Code Interpreter.

## Run locally

```bash
python3 server.py
```

The default address is `http://localhost:8090`. Set `THOR_MONITOR_PORT` to use another port.

## Service configuration

The included `thor-monitor.service` reads secrets from `/etc/thor-monitor.env`:

```ini
THOR_MONITOR_PASSWORD=replace-with-a-strong-password
THOR_IMAGE_API_KEY=replace-with-the-image-api-key
```

Copy `qwen-image/qwen-image.env.example` to `qwen-image/qwen-image.env` and replace the placeholder before starting the image service. Environment files and generated model artifacts are intentionally excluded from Git.

## Tests

Run the dependency-free regression suite from the project root:

```bash
python3 -m unittest discover -s tests -v
```

The suite covers tool-call parsing, agent tool execution, authentication, request limits, path traversal protection, image proxy history, and the AI Workspace UI contract.

## Architecture

The server entrypoint is intentionally thin around the agent subsystem:

```text
server.py          HTTP routes, telemetry, image proxy
agent/             model client, agent runtime, run states
tools/             feature-specific tool implementations
agent_tools.py     compatibility facade and Qwen tool-call parser
sandbox/           execution isolation package
storage/           persistent storage package
skills/            reusable agent guidance
tests/             regression and architecture contracts
```

`agent_tools.py` remains a compatibility entrypoint while tool implementations live in `tools/`. Future registry and permission work can therefore evolve without coupling the HTTP server to individual tools.
