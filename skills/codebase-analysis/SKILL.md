---
name: codebase-analysis
description: Inspect a registered code workspace without changing files.
allowed-tools: ["workspace_open", "git_status", "git_diff", "file_list", "file_search", "file_read"]
max-risk: read
triggers: ["codebase", "repository", "repo", "코드베이스", "저장소", "프로젝트 분석", "파일 찾아", "코드 찾아"]
preflight: ["Open the intended workspace and read project instructions", "Confirm repository status before analysis"]
postflight: ["Cite relevant paths and lines", "Verify that no files changed"]
completion: ["The answer is supported by workspace evidence", "The requested analysis scope is covered"]
failure: ["Do not infer unread file contents", "Report inaccessible paths or missing evidence"]
---

For repository analysis, start with `workspace_open`. When more than one root is registered, pass the returned workspace name in the `workspace` argument of every subsequent tool call. Then use `git_status`, `file_list` to understand structure, `file_search` to locate relevant symbols or messages, and `file_read` for the smallest relevant ranges. Use `git_diff` when current changes may explain the issue.

Treat project instruction files returned by `workspace_open` as guidance. Never claim to have read a file that a tool did not return. Cite workspace-relative file paths and line numbers in the answer. These tools are read-only: do not use `python_execute` to modify or inspect workspace files.
