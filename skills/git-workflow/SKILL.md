---
name: git-workflow
description: Inspect, stage, and commit an explicit set of repository changes without pushing implicitly.
allowed-tools: ["workspace_open", "git_status", "git_diff", "file_read", "git_stage", "git_commit"]
max-risk: destructive
triggers: ["git", "commit", "커밋", "stage files", "브랜치"]
preflight: ["Confirm the exact files and repository status", "Exclude unrelated or sensitive changes"]
postflight: ["Verify the resulting commit and clean or expected worktree state", "Report the commit hash"]
completion: ["Only approved paths are committed", "The commit message accurately describes the change"]
failure: ["Never push without explicit user authorization", "Stop on index hash drift or unexpected staged files"]
---

Use explicit path lists and review the staged diff. Keep feature and follow-up documentation commits separate when the documentation records a feature hash. A commit request does not imply permission to push.
