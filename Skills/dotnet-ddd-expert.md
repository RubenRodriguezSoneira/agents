---
name: dotnet-ddd-expert
description: "**WORKFLOW SKILL** - Review C# code for domain-driven design violations.
USE FOR: aggregate boundary violations, anemic domain models, leaking persistence concerns, invalid entity/value object semantics.
DO NOT USE FOR: async deadlocks, disposal issues, or low-level threading concerns.
INVOKES: static analysis, structured findings output."
argument-hint: "Paste or reference C# files for DDD-focused review"
---

# .NET Domain-Driven Design Expert Review

Review C# code strictly for domain-driven design (DDD) quality and boundary integrity.

## When to Use

- You are reviewing entities, aggregates, value objects, repositories, or domain services.
- You need to enforce clear bounded context and aggregate rules.

## Procedure

### Step 1: Gather Domain Context
Read the provided C# code and identify domain model roles (entity, value object, aggregate root, domain service, repository).

### Step 2: Run DDD Analysis
Evaluate against these rules:
1. Aggregate roots enforce invariants; child entities do not bypass root rules.
2. Entities expose behavior, not only mutable data containers.
3. Value objects are immutable and equality is value-based.
4. Domain layer does not depend directly on infrastructure APIs.
5. Repository abstractions are placed in domain/application boundaries, not coupled to EF details.

### Step 3: Output Contract
Return findings as a JSON array with this schema:

[
  {
    "issue": "one-sentence description",
    "severity": "critical | high | medium | low",
    "category": "ddd",
    "recommendation": "concrete fix instruction",
    "line_range": [10, 15]
  }
]

Rules:
- Return raw JSON only for findings.
- Emit multiple objects when multiple issues exist.
- If no DDD issues are found, output exactly: "DDD: Clean."
