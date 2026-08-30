---
name: thor-system-status
description: Inspect live Jetson Thor utilization, memory, storage, temperature, power, load, and uptime.
allowed-tools: ["system_status"]
max-risk: read
triggers: ["system status", "Thor status", "시스템 상태", "시스템 확인", "GPU 상태", "메모리 상태", "온도", "전력", "uptime"]
preflight: ["Identify the requested live metrics", "Treat a single sample as a snapshot"]
postflight: ["Report values with readable units", "Separate measurements from diagnosis"]
completion: ["Requested live Thor metrics are reported", "Uncertainty and sampling limits are clear"]
failure: ["Do not infer a hardware fault from one sample", "Report unavailable metrics explicitly"]
---

Use the system-status tool for questions about the current Thor machine. Report measurements with readable units and do not infer hardware faults from one sample alone.
