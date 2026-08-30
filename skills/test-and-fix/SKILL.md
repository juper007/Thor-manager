---
name: test-and-fix
description: Run a relevant test suite, repair reproducible failures, and repeat with a bounded retry loop.
allowed-tools: ["workspace_open", "git_status", "git_diff", "file_search", "file_read", "file_patch", "file_write", "test_run"]
max-risk: elevated
triggers: ["test and fix", "테스트하고 수정", "테스트 실패", "fix tests", "회귀 테스트"]
preflight: ["Identify the authoritative test command", "Separate pre-existing failures from task regressions"]
postflight: ["Re-run focused failures and the affected suite", "Inspect the final diff and test output"]
completion: ["Relevant tests pass with recorded commands and results", "No unrelated files were changed"]
failure: ["Stop after two focused repair attempts", "Return failing test names and the strongest root-cause evidence"]
---

Run the smallest useful test first. Fix product behavior rather than weakening assertions unless the specification changed. Limit repair loops, preserve complete failure evidence, and finish with the affected suite plus a diff review.
