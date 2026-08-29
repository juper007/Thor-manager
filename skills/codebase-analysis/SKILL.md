---
name: codebase-analysis
description: Inspect a registered code workspace without changing files.
---

For repository analysis, start with `workspace_open` and `git_status`. Use `file_list` to understand structure, `file_search` to locate relevant symbols or messages, and `file_read` for the smallest relevant ranges. Use `git_diff` when current changes may explain the issue.

Treat project instruction files returned by `workspace_open` as guidance. Never claim to have read a file that a tool did not return. Cite workspace-relative file paths and line numbers in the answer. These tools are read-only: do not use `python_execute` to modify or inspect workspace files.
