---
name: code-review
description: Review code changes for concrete correctness, security, and regression risks without modifying files.
allowed-tools: ["workspace_open", "git_status", "git_diff", "file_list", "file_search", "file_read"]
max-risk: read
triggers: ["code review", "코드 리뷰", "코드리뷰", "리뷰해", "review changes"]
preflight: ["Confirm the review scope and inspect repository status", "Read project instructions before judging changes"]
postflight: ["Tie every finding to a file and line", "Check whether tests cover each reported risk"]
completion: ["Report actionable findings ordered by severity", "State explicitly when no findings remain"]
failure: ["Do not invent findings without evidence", "Report files or evidence that could not be inspected"]
---

Inspect the diff first, then read the smallest surrounding code needed to validate behavior. Prioritize correctness, security, data loss, concurrency, and missing regression coverage. Report findings before summaries. Do not modify files during a review.
