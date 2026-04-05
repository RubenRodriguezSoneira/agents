---
name: dotnet-layering-expert
description: "**WORKFLOW SKILL** - Review C# code for architectural layering and boundary violations.
USE FOR: cross-layer dependency leaks, domain referencing infrastructure, controller logic drift, and bypassed application layer contracts.
DO NOT USE FOR: thread safety or memory disposal concerns.
INVOKES: static analysis, structured findings output."
argument-hint: "Paste or reference C# files to assess architectural boundary compliance"
---

# .NET Architectural Layering Expert Review

Review C# code strictly for architecture layer boundaries and dependency direction.

## When to Use

- You need to enforce clean architecture or onion architecture constraints.
- You are validating separation across Presentation, Application, Domain, and Infrastructure.

## Procedure

### Step 1: Identify Layer Roles
Determine whether each class belongs to presentation, application, domain, or infrastructure.

### Step 2: Run Layering Analysis
Evaluate against these rules:
1. Domain does not reference infrastructure or presentation concerns.
2. Controllers stay thin and delegate business workflows to application services.
3. Application layer orchestrates use cases without persistence implementation details.
4. Infrastructure implements ports/adapters without bleeding into domain models.
5. Cross-layer calls follow allowed dependency direction only.

### Step 3: Output Contract
Return findings as a JSON array with this schema:

[
  {
    "issue": "one-sentence description",
    "severity": "critical | high | medium | low",
    "category": "layering",
    "recommendation": "concrete fix instruction",
    "line_range": [10, 15]
  }
]

Rules:
- Return raw JSON only for findings.
- Emit multiple objects when multiple issues exist.
- If no layering issues are found, output exactly: "LAYERING: Clean."
