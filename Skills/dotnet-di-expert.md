---
name: dotnet-di-expert
description: "**WORKFLOW SKILL** - Review C# code for dependency injection wiring and lifetime correctness.
USE FOR: service registration mistakes, lifetime mismatches, captive dependency risks, service locator anti-patterns.
DO NOT USE FOR: async deadlock detection or memory leak analysis.
INVOKES: static analysis, structured findings output."
argument-hint: "Paste or reference C# startup/composition/service code for DI review"
---

# .NET Dependency Injection Expert Review

Review C# code strictly for dependency injection design, registration, and lifetime issues.

## When to Use

- You are validating service registration in Program.cs, Startup.cs, or composition modules.
- You need to detect scope violations and hidden runtime activation failures.

## Procedure

### Step 1: Gather Injection Context
Identify service registrations, constructor injections, factory delegates, and ambient service resolution paths.

### Step 2: Run DI Analysis
Evaluate against these rules:
1. Singleton services do not depend on scoped/transient services without factories.
2. Services are constructor-injected; avoid service locator and direct provider pulls in business logic.
3. Open generic registrations and keyed services resolve consistently with usage.
4. Registrations align with interfaces and expected implementation lifetimes.
5. Circular dependency risks are identified before runtime.

### Step 3: Output Contract
Return findings as a JSON array with this schema:

[
  {
    "issue": "one-sentence description",
    "severity": "critical | high | medium | low",
    "category": "di",
    "recommendation": "concrete fix instruction",
    "line_range": [10, 15]
  }
]

Rules:
- Return raw JSON only for findings.
- Emit multiple objects when multiple issues exist.
- If no DI issues are found, output exactly: "DI: Clean."
