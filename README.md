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
