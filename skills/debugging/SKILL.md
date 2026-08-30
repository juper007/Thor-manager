---
name: debugging
description: Reproduce a defect, isolate its root cause, and verify the diagnosis before proposing or applying a fix.
allowed-tools: ["workspace_open", "git_status", "git_diff", "file_list", "file_search", "file_read", "system_status", "shell_execute", "test_run", "file_patch"]
max-risk: destructive
triggers: ["debug", "디버깅", "버그", "오류 원인", "고쳐"]
preflight: ["Capture the observed symptom and expected behavior", "Preserve unrelated user changes"]
postflight: ["Run the narrow reproduction before the broader suite", "Review the final diff for unintended changes"]
completion: ["Root cause is supported by evidence", "A regression test demonstrates the fix"]
failure: ["Stop after bounded focused attempts", "Report the remaining uncertainty and reproduction evidence"]
---

Start from a reproducible symptom. Narrow the responsible boundary with searches and focused reads, then make the smallest justified patch. Never claim a fix from code inspection alone; run the reproduction and relevant regression tests.
