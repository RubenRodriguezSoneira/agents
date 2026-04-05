---
name: dotnet-parallel-expert
description: "**WORKFLOW SKILL** — Review C# code specifically for concurrency bugs, thread safety, and race conditions.
USE FOR: reviewing Parallel.ForEach loops, thread-safe collections, lock() statements, and shared state mutations.
DO NOT USE FOR: basic async/await checking or memory disposal.
INVOKES: static analysis, problem reporting."
argument-hint: "Paste or reference the C# code to review"
---

# .NET Concurrency & Thread-Safety Expert Review

Review C# code strictly to identify race conditions, improper synchronization, and thread-safety violations.

## When to Use

- You need to validate code that uses `Parallel` execution, `Thread`, `ThreadPool`, or concurrent data modifications.

## Procedure

### Step 1: Gather the Code
Read the provided C# code carefully. Focus entirely on shared state variables and multi-threaded execution paths.

### Step 2: Run Concurrency Analysis
Evaluate the code against the following critical thread-safety rules:
1. **Shared State Mutations:** Identify standard collections (e.g., `List<T>`, `Dictionary<K,V>`) being modified inside a `Parallel.ForEach` or `Task.Run` block. Recommend `System.Collections.Concurrent` alternatives.
2. **Improper Locking:** Flag `lock(this)`, `lock(typeof(T))`, or string locking. Recommend locking on private readonly `object` instances.
3. **Race Conditions:** Look for check-then-act operations that are not atomic (e.g., checking if a key exists in a dictionary, then adding it, without a lock or `GetOrAdd`).

### Step 3: Format Feedback
Return findings as a JSON array with this schema:

[
	{
		"issue": "one-sentence description",
		"severity": "critical | high | medium | low",
		"category": "parallel",
		"recommendation": "concrete fix instruction",
		"line_range": [10, 15]
	}
]

Rules:
- Return raw JSON only for findings.
- Emit multiple objects when multiple issues exist.
- Do not write fixed code; only provide fix instructions.
- If no parallel issues are found, output exactly: "PARALLEL: Clean."