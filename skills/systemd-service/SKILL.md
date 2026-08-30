---
name: systemd-service
description: Diagnose, change, restart, and verify a scoped systemd service safely.
allowed-tools: ["system_status", "shell_execute", "test_run", "workspace_open", "file_read", "file_search"]
max-risk: destructive
triggers: ["systemd", "서비스 재시작", "service restart", "journalctl", "systemctl"]
preflight: ["Confirm the exact unit name and host", "Capture active state, MainPID, unit settings, and recent logs"]
postflight: ["Verify active state, new PID when restarted, logs, and health endpoint", "Confirm dependent ports and services"]
completion: ["The requested unit reaches the expected state", "Post-change health checks pass"]
failure: ["Do not kill an unverified PID", "Preserve logs and stop if restart policy or privileges are insufficient"]
---

Scope every command to the named unit. Use the normal systemctl path when authorized. Before any PID-based fallback, verify the unit restart policy and re-read a PID greater than one immediately before signaling it.
