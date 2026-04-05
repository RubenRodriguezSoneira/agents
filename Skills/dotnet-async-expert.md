---
name: dotnet-async-expert
description: "**WORKFLOW SKILL** — Review C# code specifically for asynchronous programming flaws and deadlocks.
USE FOR: identifying blocking calls (.Result, .Wait()), missing ConfigureAwait, unawaited tasks, and async void usages.
DO NOT USE FOR: memory leaks, thread safety issues, or general refactoring.
INVOKES: static analysis, problem reporting."
argument-hint: "Paste or reference the C# code to review"
---

# .NET Async & Await Expert Review

Review C# code strictly to identify asynchronous programming anti-patterns, deadlocks, and performance bottlenecks.

## When to Use

- You need to validate that C# code follows non-blocking, asynchronous best practices.
- You are reviewing network calls, database queries, or file I/O operations.

## Procedure

### Step 1: Gather the Code

Read the provided C# code carefully. Ignore syntax formatting or naming conventions unless they directly relate to `Task` or `async` paradigms.

### Step 2: Run Async Analysis

Evaluate the code against the following critical async rules:

1. **Deadlock Risks:** Look for `.Result`, `.Wait()`, or `.GetAwaiter().GetResult()` on Tasks.
2. **Context Capturing:** Ensure library code uses `.ConfigureAwait(false)` where appropriate to avoid deadlocking the UI or request thread.
3. **Fire and Forget:** Identify any `async void` methods (unless they are explicitly event handlers) and flag them as critical crash risks.
4. **Unawaited Tasks:** Look for `Task` returning methods that are invoked without an `await` keyword or assignment.

### Step 3: Format Feedback

Output a concise, structured list of findings.

- Do not write fixed code; only provide the required fix instructions for the Refactor Agent.
- If no async issues are found, explicitly output: "ASYNC: Clean."
