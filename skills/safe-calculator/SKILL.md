---
name: safe-calculator
description: Calculate arithmetic precisely instead of relying on mental computation.
allowed-tools: ["calculator"]
max-risk: read
triggers: ["calculate", "calculator", "계산", "더하기", "빼기", "곱하기", "나누기", "percent", "퍼센트"]
preflight: ["Translate the requested arithmetic into an explicit expression", "Confirm units when they affect the result"]
postflight: ["Check that the result matches the tool output", "Present units and rounding clearly"]
completion: ["An exact result is returned for the requested expression", "The calculation is explained at the requested level"]
failure: ["Do not guess unsupported expressions", "Report invalid input or ambiguity"]
---

Use the calculator for arithmetic, percentages, unit formulas, and comparisons where an exact numeric result matters. Explain the result in the user's requested level of detail.
