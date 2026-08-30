---
name: jetson-optimization
description: Measure and improve Jetson workload performance without trading away stability or thermal safety.
allowed-tools: ["system_status", "workspace_open", "git_status", "git_diff", "file_search", "file_read", "shell_execute", "test_run", "file_patch"]
max-risk: destructive
triggers: ["jetson optimization", "Jetson 최적화", "성능 최적화", "GPU 최적화", "TensorRT 최적화"]
preflight: ["Record workload, latency, throughput, memory, power, and temperature baseline", "Identify active power and clock configuration"]
postflight: ["Repeat the same benchmark and compare distributions", "Check thermals, memory pressure, correctness, and sustained behavior"]
completion: ["A repeatable benchmark shows a material improvement", "Output correctness and safety limits remain satisfied"]
failure: ["Revert changes that regress correctness or stability", "Do not change power, clock, or thermal limits without explicit authorization"]
---

Optimize only against a repeatable workload. Prefer algorithmic, batching, memory, and engine improvements before system-wide clock or power changes. Report both performance gains and resource tradeoffs; a single fast sample is not sufficient evidence.
