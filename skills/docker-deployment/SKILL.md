---
name: docker-deployment
description: Build, validate, and deploy containerized services with explicit rollback and data-preservation checks.
allowed-tools: ["workspace_open", "git_status", "git_diff", "file_list", "file_search", "file_read", "shell_execute", "test_run"]
max-risk: destructive
triggers: ["docker deploy", "docker 배포", "container deployment", "컨테이너 배포", "docker compose"]
preflight: ["Identify compose files, volumes, secrets, and rollback target", "Confirm the intended host and service scope"]
postflight: ["Verify container health, logs, ports, and persistent data", "Run an application smoke test"]
completion: ["The intended image is healthy and serving traffic", "Rollback instructions and deployed revision are recorded"]
failure: ["Do not remove volumes or images as an automatic recovery step", "Preserve logs and restore the prior known-good image when authorized"]
---

Prefer immutable image tags and configuration outside the image. Never expose secret values in output. Build and test before replacing a running service, and do not prune containers, images, networks, or volumes without explicit authorization.
