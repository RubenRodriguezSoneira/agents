# Scatter-Gather POC: Multi-Expert Repository Analysis

**Phase 1: Repository Foundation** - ✅ COMPLETE

## Overview

This tool enables automated performance and code quality analysis of large .NET 8 repositories with complex DDD and DI systems by orchestrating multiple specialized AI experts to review code in parallel (scatter-gather pattern).

### Key Capabilities

- **Multi-file analysis**: Process entire .NET repositories instead of single code snippets
- **Parallel expert evaluation**: 3+ specialized experts analyze each file concurrently
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

**Note**: Requires `GITHUB_TOKEN` environment variable or `--token` argument.

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
Experts: 3 (AsyncExpert, MemoryExpert, ParallelExpert)
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

1. **AsyncExpert** (dotnet-async-expert.md)
   - Detects: `.Result` blocking, missing `ConfigureAwait`, deadlocks
   
2. **MemoryExpert** (dotnet-memory-expert.md)
   - Detects: Undisposed resources, memory leaks, event handler leaks
   
3. **ParallelExpert** (dotnet-parallel-expert.md)
   - Detects: Race conditions, thread-unsafe collections, improper locking

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
- ✅ Phase 1: CLI argument parsing and flexibility
- ⏳ Phase 2: Metadata extraction and type awareness
- ⏳ Phase 3: DDD and DI expert specialists
- ⏳ Phase 4: Large-scale optimization and caching

---

**Status**: Production-ready for Phase 1. Ready for Phase 2 metadata extraction.
