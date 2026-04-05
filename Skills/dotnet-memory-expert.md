---
name: dotnet-memory-expert
description: "**WORKFLOW SKILL** — Review C# code specifically for memory leaks and garbage collection bottlenecks.
USE FOR: identifying undisposed IDisposable objects, improper stream handling, large object heap (LOH) allocations, and static event leaks.
DO NOT USE FOR: async deadlocks, race conditions, or general style refactoring.
INVOKES: static analysis, problem reporting."
argument-hint: "Paste or reference the C# code to review"
---

# .NET Memory & Garbage Collection Expert Review

Review C# code strictly to identify memory leaks, unmanaged resource mismanagement, and excessive heap allocations.

## When to Use

- You need to validate that C# code safely handles resources like Streams, Database Connections, WebResponses, or unmanaged handles.

## Procedure

### Step 1: Gather the Code
Read the provided C# code carefully. Focus entirely on object lifecycle, instantiation, and disposal.

### Step 2: Run Memory Analysis
Evaluate the code against the following critical memory rules:
1. **IDisposable Management:** Identify classes that implement `IDisposable` (e.g., `MemoryStream`, `HttpClient`, `SqlConnection`) that are not wrapped in a `using` statement or block.
2. **Event Leaks:** Check for event subscriptions (`+=`) that lack a corresponding unsubscribe (`-=`), especially on static classes or long-lived services.
3. **Large Allocations:** Flag repeated allocations of large arrays or strings inside tight loops (recommend `StringBuilder` or `ArrayPool`).

### Step 3: Format Feedback
Return findings as a JSON array with this schema:

[
	{
		"issue": "one-sentence description",
		"severity": "critical | high | medium | low",
		"category": "memory",
		"recommendation": "concrete fix instruction",
		"line_range": [10, 15]
	}
]

Rules:
- Return raw JSON only for findings.
- Emit multiple objects when multiple issues exist.
- Do not write fixed code; only provide fix instructions.
- If no memory issues are found, output exactly: "MEMORY: Clean."