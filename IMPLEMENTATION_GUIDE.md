# Scatter-Gather POC: Multi-Expert Repository Analysis

**Phase 1-4 Delivery Status** - ✅ CORE IMPLEMENTATION COMPLETE (validation and hardening ongoing)

## Overview

This tool enables automated performance and code quality analysis of large .NET 8 repositories with complex DDD and DI systems by orchestrating multiple specialized AI experts to review code in parallel (scatter-gather pattern).

### Key Capabilities

- **Multi-file analysis**: Process entire .NET repositories instead of single code snippets
- **Parallel expert evaluation**: 6 specialized experts analyze each file concurrently
- **Hot-path prioritization**: Focuses on Controllers, Services, and Handler classes first
- **Batch processing**: Efficiently handles 100+ file repositories with configurable batching
- **Structured reporting**: JSON output with findings by severity (Critical/High/Medium/Low)
- **Repository flexibility**: Supports GitHub repos (public/private), local paths, or test code

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### 1. Default Test Code (No Repository Required)

```bash
python scatter_gather_poc.py
```

This analyzes a built-in example code snippet with intentional issues (async deadlock, memory leak, race condition).

**Note**: Use `--dry-run` to test configuration without `GITHUB_TOKEN`. Non-dry runs require `GITHUB_TOKEN`.

### 2. Analyze Local .NET Repository

```bash
python scatter_gather_poc.py --local C:\path\to\dotnet\repo --max-files 20
```

- `--local`: Path to existing .NET repository
- `--max-files`: Limit analysis to first N files (for testing)
- `--no-hot-path-only`: Include all files (default: prioritizes Controllers/Services/Handlers)

### 3. Analyze Public GitHub Repository

```bash
python scatter_gather_poc.py --repo dotnet/runtime --max-files 10
```

- `--repo`: GitHub repository in `owner/repo` format
- `--branch`: Git branch (default: main)

### 4. Analyze Private GitHub Repository

```bash
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
python scatter_gather_poc.py --repo myorg/private-repo --token $GITHUB_TOKEN --max-files 15
```

### 5. Custom Output and Batch Configuration

```bash
python scatter_gather_poc.py \
  --local C:\repo \
  --output report_2026-04-05.json \
  --batch-size 10 \
  --max-files 100 \
  --no-hot-path-only
```

## CLI Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--repo` | str | - | GitHub repo (`owner/repo`) |
| `--local` | path | - | Local repository path |
| `--branch` | str | `main` | Git branch to analyze |
| `--token` | str | `$GITHUB_TOKEN` | GitHub token for private repos |
| `--output` | path | `analysis_report.json` | Report output file |
| `--batch-size` | int | `5` | Files per batch |
| `--max-files` | int | - | Limit to first N files |
| `--no-hot-path-only` | flag | - | Analyze all files (default: hot paths only) |
| `--max-tokens-per-batch` | int | - | Cap estimated tokens per batch (chars/4 heuristic) |
| `--max-concurrency` | int | `5` | Max concurrent expert requests |
| `--cache-dir` | path | `.sg_cache` | State/checkpoint storage path |
| `--resume` | flag | - | Resume from checkpoint |
| `--dry-run` | flag | - | Print projected calls and batches without model requests |
| `--roslyn-timeout` | int | `300` | Roslyn metadata extraction timeout (seconds) |

## Output

### Console Output

```
======================================================================
ANALYSIS SCOPE
======================================================================
Repository: myorg/myrepo
Root: C:\repos\myrepo
Total C# files found: 342
Files to analyze: 50
Experts: 6 (AsyncExpert, MemoryExpert, ParallelExpert, DDDExpert, DIExpert, LayeringExpert)
Output: analysis_report.json
======================================================================

[Batch 1/10] Analyzing 5 files...
  ✓ Batch complete: 12 findings (2 critical)

[Batch 2/10] Analyzing 5 files...
  ✓ Batch complete: 8 findings (1 critical)

...

======================================================================
ANALYSIS SUMMARY
======================================================================
Files analyzed: 50
Total findings: 120

Findings by severity:
  CRITICAL: 8
  HIGH: 24
  MEDIUM: 56
  LOW: 32

Findings by category:
  async: 45
  memory: 32
  parallel: 28
  ...

Expert activation:
  AsyncExpert: 45 findings
  MemoryExpert: 32 findings
  ParallelExpert: 28 findings

Files with critical issues: 8
Files with no findings: 12
======================================================================

TOP CRITICAL ISSUES:
======================================================================

src/Services/OrderService.cs
  Expert: AsyncExpert
  Issue: Potential deadlock: .Result blocks on async DbContext operation
  Recommendation: Use async/await through entire call chain; remove .Result

src/Controllers/PaymentController.cs
  Expert: MemoryExpert
  Issue: HttpClient created without disposal in loop; potential socket exhaustion
  Recommendation: Use static HttpClient or inject via DI

... and 6 more critical issues
```

### JSON Report

```json
{
  "repository_name": "myorg/myrepo",
  "analyzed_at": "2026-04-05T14:32:15.123456",
  "scope": {},
  "summary_metrics": {
    "total_files_analyzed": 50,
    "total_findings": 120,
    "findings_by_severity": {"critical": 8, "high": 24, "medium": 56, "low": 32},
    "findings_by_category": {"async": 45, "memory": 32, "parallel": 28},
    "expert_activation": {"AsyncExpert": 45, "MemoryExpert": 32, "ParallelExpert": 28},
    "files_with_critical": 8,
    "files_with_no_findings": 12
  },
  "file_feedbacks": [
    {
      "file_path": "C:\\repos\\repo\\src\\Services\\OrderService.cs",
      "relative_path": "src\\Services\\OrderService.cs",
      "findings": [
        {
          "expert": "AsyncExpert",
          "issue": "Potential deadlock: .Result blocks on async DbContext query",
          "severity": "critical",
          "recommendation": "Use async/await; remove .Result",
          "category": "async"
        }
      ],
      "expert_summaries": {
        "AsyncExpert": "Potential deadlock: .Result blocks on async DbContext query"
      }
    }
  ]
}
```

## Architecture

### New Modules

#### `repo_ingestion.py`
- **GitHubRepoFetcher**: Clone repos from GitHub, handle auth, local caching
- **ProjectGraphBuilder**: Parse .csproj files, discover dependencies, detect cycles
- **CSFileCollector**: Collect .cs files with filtering and prioritization
- **batch_files()**: Divide files into batches for efficient processing

#### `results_aggregator.py`
- **Finding**: Structured finding with expert, issue, severity, recommendation
- **FileFeedback**: Aggregated findings per file
- **RepositoryReport**: Complete report with metrics
- **ResultsAggregator**: Create findings, aggregate, save/print reports

#### `scatter_gather_poc.py` (Enhanced)
- **analyze_file()**: Single-file expert analysis with context
- **analyze_batch()**: Parallel batch processing
- **main()**: CLI orchestration, repo handling, batching, aggregation

### Processing Flow

```
User Input (--repo/--local/default)
    ↓
GitHubRepoFetcher OR local path
    ↓
CSFileCollector (discovers .cs files)
    ↓
Prioritize hot paths (Controllers/Services/Handlers)
    ↓
Create batches (batch_size = 5 default)
    ↓
For each batch:
  - For each file:
    - Send source code to all experts in parallel
    - Experts return findings
    - Create FileFeedback
  - Print batch summary
    ↓
Aggregate all findings into RepositoryReport
    ↓
Save JSON + print summary to console
```

## Expert Specialists (Current)

1. **AsyncExpert** (`dotnet-async-expert.md`)
  - Detects: `.Result` blocking, missing `ConfigureAwait`, deadlocks

2. **MemoryExpert** (`dotnet-memory-expert.md`)
  - Detects: Undisposed resources, memory leaks, event handler leaks

3. **ParallelExpert** (`dotnet-parallel-expert.md`)
  - Detects: Race conditions, thread-unsafe collections, improper locking

4. **DDDExpert** (`dotnet-ddd-expert.md`)
  - Detects: Aggregate boundary issues, anemic domain models, invariant leaks

5. **DIExpert** (`dotnet-di-expert.md`)
  - Detects: Lifetime mismatches, captive dependencies, service locator anti-patterns

6. **LayeringExpert** (`dotnet-layering-expert.md`)
  - Detects: Layer boundary violations, dependency direction drift

## Future Phases

### Phase 2: Metadata Extraction (planned)
- Roslyn AST integration for accurate symbol resolution
- Type information and inheritance chains
- Dependency graph enhancement
- Reduce false positives

### Phase 3: DDD + DI Experts (planned)
- DomainDrivenDesignExpert: Entity/aggregate violations, context boundaries
- DependencyInjectionExpert: Registration issues, scope mismatches, circular deps
- ArchitecturalLayeringExpert: Layer boundary violations

### Phase 4: Large-Scale Optimization (planned)
- Token-aware batching for cost control
- Incremental analysis (only changed files)
- Results deduplication
- Progress persistence and resumption

## Troubleshooting

### No GITHUB_TOKEN Set

```
Error: GITHUB_TOKEN environment variable is not set.
```

**Solution**: Either provide `--token` or set environment variable:
```bash
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
```

### Git Clone Fails

```
Error: git command not found. Ensure git is installed.
```

**Solution**: Install git or use `--local` with existing repository.

### File Unreadable Errors

Large files (10k+ LOC) are automatically truncated to 15000 characters to avoid token limits. Check logs for details.

### Analysis Too Slow

- Use `--batch-size 10` to increase parallelism
- Use `--max-files 20` to limit scope
- Check `--batch-size` and adjust based on file size distribution

## Getting Started

1. **Test with default code** (verify setup):
   ```bash
   export GITHUB_TOKEN=ghp_test_token  # Can be placeholder for PoC
   python scatter_gather_poc.py
   ```

2. **Test with local repository**:
   ```bash
   python scatter_gather_poc.py --local C:\path\to\dotnet\repo --max-files 10
   ```

3. **Review generated report**:
   ```bash
   cat analysis_report.json | jq '.summary_metrics'
   ```

4. **Customize analysis**:
   ```bash
   python scatter_gather_poc.py \
     --local C:\repo \
     --output my_report.json \
     --batch-size 10 \
     --no-hot-path-only \
     --max-files 100
   ```

## Implementation Status

- ✅ Phase 1: Repository ingestion and file collection
- ✅ Phase 1: Multi-file batch analysis orchestration
- ✅ Phase 1: Results aggregation and JSON reporting
- ✅ Phase 1: Orchestrator restored (`scatter_gather_poc.py`)
- ✅ Phase 2: Metadata extraction (Roslyn + heuristic fallback)
- ✅ Phase 3: DDD/DI/Layering expert specialists
- ✅ Phase 4: Token-aware batching and concurrency controls
- ✅ Phase 4: Incremental caching, resume checkpoints, deduplication
- ✅ Tests: Parsing, metadata fallback, state persistence, resume, dedup

---
## Plan: Phase 2-4 End-to-End Delivery (v2)

Deliver Phases 2, 3, and 4 by first restoring the missing orchestration baseline, then adding Roslyn-backed metadata extraction (with heuristic fallback), expanding expert coverage (DDD/DI/layering), and implementing scalability controls (token-aware batching, incremental git+hash analysis, deduplication, and checkpoint resume).

> **Status update**: The orchestrator gap has been resolved. `scatter_gather_poc.py`
> is restored and currently wires metadata enrichment, incremental caching, resume,
> deduplication, and dry-run controls.

### Expert Output Schema (target)

All experts MUST return a JSON array. The parser accepts this schema and falls back to
plain-text extraction for backward compatibility.

```json
[
  {
    "issue": "string — one-sentence description of the problem",
    "severity": "critical | high | medium | low",
    "category": "async | memory | parallel | ddd | di | layering",
    "recommendation": "string — fix instruction for the Refactor Agent",
    "line_range": [10, 15]
  }
]
```

### New File Manifest

| Artifact | Path | Purpose |
|----------|------|---------|
| Main orchestrator | `scatter_gather_poc.py` | Restored Phase 1 entry point |
| Roslyn helper project | `RoslynMetadataExtractor/` | .NET project emitting metadata JSON |
| Python metadata consumer | `metadata_enricher.py` | Ingests Roslyn JSON, enriches prompts |
| State & checkpoint manager | `analysis_state.py` | Incremental state, fingerprints, resume |
| DDD expert skill | `Skills/dotnet-ddd-expert.md` | Domain-driven design analysis |
| DI expert skill | `Skills/dotnet-di-expert.md` | Dependency injection analysis |
| Layering expert skill | `Skills/dotnet-layering-expert.md` | Architectural layer boundary analysis |
| Integration tests | `tests/` | Pytest suite for parsing, state, dedup |

### Steps

1. **Baseline Recovery** — Recreate `scatter_gather_poc.py` with the documented Phase 1 flow: CLI parsing, repo/local/default input, file collection via `CSFileCollector`, batching via `batch_files()`, async expert scatter-gather, `ResultsAggregator` report save and summary print. Fix the `analyzed_at` bug in `results_aggregator.py` (use `datetime.now().isoformat()` instead of file mtime). This is the mandatory foundation for all later steps.

2. **Structured Finding Parsing** — Upgrade `ResultsAggregator.create_file_feedback()` to prefer the JSON schema above, with plain-text fallback for backward compatibility. Normalize severity to lowercase enum, category to stable mapping (`async`, `memory`, `parallel`, `ddd`, `di`, `layering`). Add pytest coverage for both JSON and plain-text parsing paths. **Depends on step 1.**

3. **Expert Expansion** — Add `Skills/dotnet-ddd-expert.md`, `Skills/dotnet-di-expert.md`, `Skills/dotnet-layering-expert.md` using current frontmatter conventions. Each skill MUST emit the JSON schema above (with plain-text "CATEGORY: Clean." when no findings). Register all six experts in `scatter_gather_poc.py`. **Depends on step 2** (parser must handle JSON before new experts start emitting it).

4. **Roslyn Pipeline Bootstrap** — Add `RoslynMetadataExtractor/` .NET project using MSBuildWorkspace + Compilation + SemanticModel to emit repository metadata JSON (symbols, type hierarchy, inheritance, constructor dependencies, call references, project graph). Ship as a self-contained local project invoked via `dotnet run` subprocess from Python. **Depends on step 1. High-risk item — see risk mitigation below.**

5. **Heuristic Metadata Fallback** — Implement regex/pattern-based metadata extraction in `metadata_enricher.py` that runs when Roslyn extraction is unavailable (missing SDK, private NuGet feeds, multi-TFM build failures). Extracts: class name, base types, interface implementations, constructor parameters, namespace, `[ApiController]`/`[HttpGet]` attributes. This is a **first-class deliverable**, not optional. **Parallel with step 4.**

6. **Python Metadata Integration** — Extend `metadata_enricher.py` to consume Roslyn JSON (preferred) or heuristic output (fallback) and enrich per-file expert prompts with structured context (type kind, namespace, base types, constructor injections, related symbols/files, inferred architectural layer). **Depends on steps 4 and 5.**

7. **Token-Aware Batching and Concurrency Controls** — Extend the existing `batch_files(max_tokens_per_batch=)` in `repo_ingestion.py` (already supports chars/4 heuristic). Surface CLI flags: `--max-tokens-per-batch`, `--max-concurrency`. Add `asyncio.Semaphore`-based request throttling during expert fan-out. Add `--dry-run` mode that reports projected file count, batch count, and estimated API calls without invoking any model. **Depends on step 1.**

8. **Incremental Analysis (Git + Hash)** — Add `analysis_state.py` with persisted state: repository key, config signature, last analyzed commit SHA, per-file SHA-256 fingerprints, cached per-file `FileFeedback`. Use `git diff` when available to compute changed candidates; fall back to fingerprint-based invalidation when git context is unavailable. Scope: direct file-change detection only — defer transitive dependency invalidation to a future phase. **Depends on step 1.**

9. **Progress Persistence and Resume** — Checkpoint completed files and partial feedback to `analysis_state.py` after each batch. Support `--resume` flag that restores checkpoint state, skips completed work, and clears checkpoint only after successful completion. **Depends on steps 8 and 2.**

10. **Finding Deduplication** — Deduplicate findings by normalized signature (expert, category, severity, canonicalized issue text), both within a single file and across resumed/cached merges. Add pytest coverage for dedup edge cases. **Depends on step 2.**

11. **Integration Tests** — Add `tests/` directory with pytest suite covering: structured JSON parsing, plain-text fallback parsing, severity/category normalization, incremental state persistence and invalidation, checkpoint resume with partial state, finding deduplication. Use a small fixture `.cs` file set. **Parallel with steps 8-10.**

12. **Reporting and Docs Update** — Extend report JSON scope with: `metadata_extraction_status` (roslyn/heuristic/none), `cache_hits`, `resumed_count`, `files_analyzed_fresh` vs `files_reused`, category metrics for `ddd`/`di`/`layering`. Update this guide and `README.md` for new CLI flags, Roslyn prerequisites, expert list, resume workflow, and `--dry-run` usage. **Depends on steps 2-10.**

13. **Validation** — Run `pytest tests/`. Execute representative dry-runs: default snippet, local repo with `--max-files 10`, incremental rerun (expect cache hits), interrupted-and-resume run, `--dry-run` mode. Verify expected report fields and behavior. **Depends on all prior steps.**

### Dependency Graph

```
Step 1 (Baseline)
  ├── Step 2 (Parsing) ──┬── Step 3 (New Experts)
  │                      ├── Step 9 (Resume)
  │                      ├── Step 10 (Dedup)
  │                      └── Step 12 (Docs)
  ├── Step 4 (Roslyn) ───┤
  ├── Step 5 (Heuristic) ─┤
  │                       └── Step 6 (Metadata Integration)
  ├── Step 7 (Batching/Concurrency)
  ├── Step 8 (Incremental) ── Step 9 (Resume)
  └── Step 11 (Tests, parallel with 8-10)

Step 13 (Validation) depends on all.
```

### Risk Mitigation: Roslyn Integration

MSBuildWorkspace requires the target repo's full dependency graph to be restorable (`dotnet restore` must succeed). This will fail for repos with:
- Private NuGet feeds without configured credentials
- Conditional or multi-TFM build targets
- Missing SDKs or workloads

**Mitigation**: Step 5 (heuristic fallback) is a first-class deliverable that ships alongside Roslyn. The orchestrator transparently degrades: Roslyn JSON → heuristic extraction → no metadata (experts still run, just without enrichment). `metadata_extraction_status` in the report indicates which path was used.

### Cost and Rate-Limit Controls

With 6 experts × N files, API call volume scales quickly. Controls:
- `--max-concurrency` (default: 5) limits parallel expert requests via `asyncio.Semaphore`
- `--max-tokens-per-batch` caps total source tokens per batch
- `--max-files` caps total analysis scope
- `--dry-run` reports projected call count and batch layout without invoking any model
- Incremental mode (step 8) skips unchanged files entirely

### Relevant Files

| File | Role |
|------|------|
| `scatter_gather_poc.py` (new) | Main orchestrator — restored in step 1 |
| `repo_ingestion.py` | Reuse/extend batching and project graph utilities |
| `results_aggregator.py` | Structured parser, normalization, dedup, report enhancements |
| `metadata_enricher.py` (new) | Roslyn JSON + heuristic metadata consumer |
| `analysis_state.py` (new) | Incremental state, fingerprints, checkpoint/resume |
| `RoslynMetadataExtractor/` (new) | .NET Roslyn helper project |
| `Skills/dotnet-async-expert.md` | Existing — add JSON output schema guidance |
| `Skills/dotnet-memory-expert.md` | Existing — add JSON output schema guidance |
| `Skills/dotnet-parallel-expert.md` | Existing — add JSON output schema guidance |
| `Skills/dotnet-ddd-expert.md` (new) | DDD expert skill |
| `Skills/dotnet-di-expert.md` (new) | DI expert skill |
| `Skills/dotnet-layering-expert.md` (new) | Layering expert skill |
| `tests/` (new) | Pytest integration tests |
| `requirements.txt` | Add test dependencies if needed |
| `README.md` | User-facing docs update |
| `IMPLEMENTATION_GUIDE.md` | Architecture/status update |

### Verification

1. **Startup**: `python scatter_gather_poc.py --help` lists all Phase 4 flags (`--max-tokens-per-batch`, `--max-concurrency`, `--resume`, `--dry-run`, `--cache-dir`).
2. **Roslyn extraction**: `dotnet run --project RoslynMetadataExtractor` produces metadata JSON for a small test repo with symbols/types/inheritance/project references.
3. **Heuristic fallback**: Running without .NET SDK installed still produces metadata via regex extraction; report shows `metadata_extraction_status: "heuristic"`.
4. **Expert registration**: Console scope lists six experts; each contributes activations in report metrics.
5. **Structured parsing**: Experts returning JSON findings are parsed into multiple findings per expert per file; plain-text responses produce stable findings via fallback.
6. **Incremental**: Second run with no code changes reuses cache and skips analysis; modifying one .cs file triggers only that file.
7. **Resume**: Interrupt run mid-way, relaunch with `--resume`, verify completed files are skipped and final report is complete without duplication.
8. **Dedup**: Repeated equivalent findings across retries/resume are collapsed to single canonical entries.
9. **Dry-run**: `--dry-run` outputs batch layout and projected API call count, makes zero model requests.
10. **Reporting**: Output JSON includes `metadata_extraction_status`, `cache_hits`, `resumed_count`, `files_analyzed_fresh`, and category metrics for `ddd`/`di`/`layering`.
11. **Tests**: `pytest tests/` passes with coverage for parsing, state, and dedup modules.
12. **Documentation**: README and this guide match actual CLI behavior and new prerequisites.

### Decisions

- Roslyn fallback is a first-class deliverable (step 5), not optional.
- Structured JSON parsing (step 2) must precede expert expansion (step 3) to avoid format mismatch.
- Transitive dependency invalidation is deferred — incremental mode detects direct file changes only.
- Output schema is versioned via a `"schema_version": 2` field in report JSON for safe consumer migration.
- Automated pytest suite is required — manual-only verification does not scale for incremental/resume/dedup.
- Existing `batch_files(max_tokens_per_batch=)` in `repo_ingestion.py` is reused, not reimplemented.
