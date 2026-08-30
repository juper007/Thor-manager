# Jetson Thor Monitor

Real-time monitoring dashboard and AI workspace for NVIDIA Jetson Thor.

Features include system telemetry, real-time streaming Qwen chat, Markdown rendering, image generation/editing, web-enabled agent tools, and an isolated Python Code Interpreter.

Jetson Thor 운영 배포는 [배포 런북](docs/deployment-runbook.md)을 따른다. 서버는 Git checkout이 아니므로 `git pull` 대신 런북의 Git archive 절차를 사용한다.

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
THOR_SESSION_DB=/home/juper007/thor-monitor/data/sessions.db
THOR_SESSION_MAX_AGE_DAYS=30
THOR_SESSION_KEEP_RECENT=100
THOR_APPROVAL_TTL_SECONDS=300
THOR_WORKSPACE_ROOTS=/home/juper007/thor-monitor
THOR_CONTEXT_CHARACTER_LIMIT=120000
THOR_VERIFY_AGENT=0
THOR_SCHEDULER_ENABLED=0
THOR_SCHEDULER_POLL_SECONDS=5
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

`POST /api/chat` accepts an optional `run_id` containing 1–64 letters, numbers, underscores, or hyphens. Clients that need cancellation should generate and send the ID before starting the request. Send `"stream":true` to receive newline-delimited `start`, `delta`, and `final` events while the model generates. The UI renders Markdown incrementally from `delta` content; the `final` event carries authoritative `final_content`, tool results, sources, and `run_state` without repeating the answer as another displayed message. Runs can be inspected with `GET /api/chat/runs/{run_id}` and cancelled with `POST /api/chat/cancel` using `{"run_id":"..."}`.

The optional `mode` is `ask`, `plan`, or `agent` and defaults to `agent`. Ask mode answers without executing tools, Plan mode returns a plan without executing tools, and Agent mode uses the normal permission-controlled tool workflow. Run snapshots persist `run.mode`, `plan.created`, and `plan.step` events so clients can render live plan progress. Successful `git_diff`, `file_write`, and `file_patch` completion events include a bounded unified-diff preview, while `test_run` events include redacted command, exit-code, timing, stdout, and stderr previews for the workspace UI.

Run state, messages, events, and tool results are persisted in SQLite. The default database is `data/sessions.db`; override it with `THOR_SESSION_DB`. At startup, interrupted sessions are marked failed with a restart reason so they can be inspected or resumed safely.

```text
GET  /api/chat/sessions?limit=50&offset=0
GET  /api/chat/sessions/{run_id}
POST /api/chat/sessions/{run_id}/resume   {"run_id":"optional-new-run-id"}
GET  /api/chat/approvals?run_id={run_id}&status=pending
POST /api/chat/approvals/{approval_id}    {"decision":"allow|deny","scope":"once|session|always_tool"}
GET  /api/chat/permission-grants
DELETE /api/chat/permission-grants/{grant_id}
```

Only failed or cancelled sessions can be resumed. Retention defaults to 30 days while preserving the 100 most recently updated sessions. Credential-like fields and inline secrets are redacted before storage.

The AI Workspace lists recent sessions in pages of 20. Selecting one restores its messages, run mode, plan, tool cards, diffs, and test results. Failed and cancelled sessions expose a Resume action that preserves the stored run mode and creates a linked run.

Read tools run automatically. Safe-write, elevated, and destructive tools wait for an authenticated approval before execution. Waiting releases AI concurrency capacity so unrelated chats can continue. Approval requests expire after `THOR_APPROVAL_TTL_SECONDS`; the default is 300 seconds. The engine records the exact argument hash and refuses execution if arguments change after approval. `once` applies to one request, `session` grants the same tool for that run, and `always_tool` persists for future runs until revoked through the permission-grants API. Denying a tool blocks further attempts to use that tool in the same run.

## Read-only workspace tools

`THOR_WORKSPACE_ROOTS` is an OS path-separator-delimited allowlist of directories the agent may inspect. It defaults to the server working directory. The workspace tools can select a registered root, list and read bounded text files, search with ripgrep or a safe Python fallback, and inspect filtered Git status/diffs. Resolved paths and symlink targets must remain inside the selected root; hidden, protected, and Git-ignored files are excluded. Selection is stateless: when multiple roots are registered, pass the exact `workspace` name or root returned by `workspace_open` to every subsequent workspace tool.

Coding tools provide SHA-256 guarded `file_patch` and `file_write`, Docker-isolated `shell_execute` and `test_run`, and local-only `git_stage`/`git_commit`. File changes return a unified diff and require explicit `apply=true`; existing files are rechecked immediately before atomic replacement. Tests mount the workspace read-only, while writable shell access requires destructive approval. `git_stage` accepts only explicit path/hash pairs and returns an index hash that `git_commit` must match; commit failures are never treated as success and no tool pushes.

Run the optional live web-tool smoke test on a networked host:

```bash
python3 scripts/smoke_web_tool.py
```

## Advanced agent services

Stage 10 adds a managed MCP stdio client, automatic context compaction, project-scoped long-term memory, Git worktree isolation, an optional independent verification model pass, persistent interval schedules, public-HTTPS webhook notifications, and a 24-hour usage/performance dashboard on the main monitor page.

The management API is authenticated with the same Basic authentication as the rest of Thor Monitor:

```text
GET/POST/DELETE /api/advanced/mcp[/{name}]
POST            /api/advanced/mcp/{name}/connect
POST            /api/advanced/mcp/{name}/disconnect
GET/POST/DELETE /api/advanced/memories[/{key}]?project={project-key}
GET/POST         /api/advanced/schedules
POST             /api/advanced/schedules/{id}
POST/DELETE      /api/advanced/notifications[/{id}]
GET/POST/DELETE /api/advanced/worktrees[/{name}]
GET              /api/advanced/usage?since={unix-seconds}
```

MCP tools are exposed to the model as `mcp_list` and `mcp_call`; calls to external MCP tools are classified as elevated and pass through the existing approval engine. Schedules and the verification pass are disabled by default. Enable them explicitly with `THOR_SCHEDULER_ENABLED=1` and `THOR_VERIFY_AGENT=1`. Notification targets must use public HTTPS addresses and are resolved to pinned public IPs to prevent DNS-rebinding SSRF.

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

Skills are discovered from direct child `skills/*/SKILL.md` files. Every skill declares JSON-list frontmatter fields for `allowed-tools`, `triggers`, `preflight`, `postflight`, `completion`, and `failure`, plus a `max-risk` value (`read`, `safe_write`, `elevated`, or `destructive`). The catalog rejects malformed metadata, unknown tools, symlink escapes, duplicate names, and tools above the declared risk ceiling. Matching skills are selected from the latest user request, rendered into the system prompt, and their tool allowlist is enforced by the runtime.
