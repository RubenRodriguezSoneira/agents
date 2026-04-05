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
Output a concise, structured list of findings. 
- Do not write fixed code; only provide the required fix instructions for the Refactor Agent.
- If no memory issues are found, explicitly output: "MEMORY: Clean."