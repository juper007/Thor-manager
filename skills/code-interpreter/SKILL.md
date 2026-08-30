---
name: code-interpreter
description: Write and execute Python when a request benefits from non-trivial computation, data analysis, algorithms, validation, or simulation.
allowed-tools: ["python_execute"]
max-risk: elevated
triggers: ["python", "파이썬", "코드 인터프리터", "data analysis", "데이터 분석", "simulation", "시뮬레이션"]
preflight: ["Confirm that execution materially improves correctness", "Keep inputs free of secrets and host-file assumptions"]
postflight: ["Check stdout, errors, and computed values", "Explain assumptions and reproducibility limits"]
completion: ["The requested computation completes with inspectable output", "The answer uses the returned values exactly"]
failure: ["Make at most one focused correction", "Report the execution error and unresolved limitation"]
---

Use Python when executing code materially improves correctness or lets you verify a result. Print the values needed for the final answer. The execution environment is disposable, has no network or host-file access, and enforces resource limits. Do not claim that code ran unless the tool returned a result. If execution fails, inspect the error and make at most one focused correction before explaining the limitation. Use the calculator instead for simple arithmetic.
