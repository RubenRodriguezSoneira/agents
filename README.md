# agents

## Scatter-Gather POC: Multi-Expert Repository Analysis

Enhanced Python implementation of the scatter-gather pattern for automated code analysis across entire .NET repositories with multiple specialized AI experts.

**Phase 1 Status**: ✅ Complete - Repository Foundation with multi-file support

### Architecture Overview

This tool enables analysis of large .NET 8 repositories with complex DDD and DI systems using a scatter-gather pattern:

1. **Scatter**: Distribute code to multiple specialist experts in parallel
   - AsyncExpert: Detects deadlocks, blocking operations, ConfigureAwait issues
   - MemoryExpert: Identifies resource leaks, disposal issues, event handler leaks
   - ParallelExpert: Finds race conditions, thread-safety violations, locking problems

2. **Gather**: Collect findings from all experts and aggregate by severity

3. **Refactor**: Synthesize findings into corrected code (with expert feedback)

### Key Features

- **Multi-file analysis**: Process entire repositories, not just code snippets
- **Intelligent batching**: Configurable batch sizes for efficient API usage
- **Hot-path prioritization**: Focuses on Controllers, Services, Handlers (for performance analysis)
- **Flexible input**: GitHub repos (public/private), local paths, or default test code
- **Structured reporting**: JSON output with severity levels, categories, and expert activation metrics
- **Repository context**: Includes dependency information and architectural understanding

### Quick Start

#### 1. Default Test Code (verification)

```bash
export GITHUB_TOKEN=ghp_test_token
python scatter_gather_poc.py
```

Analyzes built-in example code with intentional async, memory, and parallel issues.

#### 2. Analyze Local Repository

```bash
python scatter_gather_poc.py --local C:\path\to\dotnet\repo --max-files 20
```

#### 3. Analyze GitHub Repository

```bash
python scatter_gather_poc.py --repo dotnet/runtime --max-files 10
```

#### 4. Private Repository

```bash
export GITHUB_TOKEN=ghp_xxxxx
python scatter_gather_poc.py --repo myorg/private --max-files 15
```

### Installation and Setup

```bash
pip install -r requirements.txt
```

Required environment variables:
- `GITHUB_TOKEN` (required for API calls)
- `GITHUB_MODEL` (optional, default: `gpt-4.1`)

### Command-Line Options

```
--repo REPO                 GitHub repo (owner/repo format)
--local LOCAL               Local repository path
--branch BRANCH             Git branch (default: main)
--token TOKEN              GitHub token (overrides GITHUB_TOKEN env)
--output OUTPUT            Report output file (default: analysis_report.json)
--batch-size BATCH_SIZE    Files per batch (default: 5)
--max-files MAX_FILES      Limit to first N files (useful for testing)
--no-hot-path-only         Analyze all files (default: Controllers/Services/Handlers only)
--help                     Show help message
```

### Output

**Console Summary**:
- Batch-by-batch progress with finding counts
- Severity distribution (Critical/High/Medium/Low)
- Category breakdown (async, memory, parallel, etc.)
- Expert activation statistics
- Top critical issues with recommendations

**JSON Report** (`analysis_report.json`):
```json
{
  "repository_name": "myorg/myrepo",
  "analyzed_at": "2026-04-05T14:32:15",
  "summary_metrics": {
    "total_files_analyzed": 50,
    "total_findings": 120,
    "findings_by_severity": {"critical": 8, "high": 24, "medium": 56, "low": 32},
    "files_with_critical": 8
  },
  "file_feedbacks": [...]
}
```

### Project Structure

```
.
├── scatter_gather_poc.py      # Main orchestration + CLI
├── repo_ingestion.py          # Repository discovery and file collection
├── results_aggregator.py      # Finding aggregation and reporting
├── Skills/
│   ├── dotnet-async-expert.md
│   ├── dotnet-memory-expert.md
│   └── dotnet-parallel-expert.md
├── requirements.txt
└── IMPLEMENTATION_GUIDE.md    # Detailed documentation
```

### Implementation Status

- ✅ **Phase 1: Repository Foundation**
  - Multi-file analysis and batch processing
  - GitHub repo cloning (public/private)
  - Local repository support
  - Hot-path prioritization
  - Structured results aggregation
  - JSON reporting

- ⏳ **Phase 2: Metadata Extraction** (planned)
  - Roslyn AST integration
  - Type resolution and symbol tables
  - Dependency graph enhancement

- ⏳ **Phase 3: DDD + DI Experts** (planned)
  - Domain-driven design analysis
  - Dependency injection pattern detection
  - Architectural layering compliance

- ⏳ **Phase 4: Scale Optimization** (planned)
  - Token-aware batching
  - Incremental analysis
  - Results deduplication and caching

### Example: Analyzing a .NET Project

```bash
# Analyze OrderService project (first 20 files)
python scatter_gather_poc.py \
  --local C:\Repos\LargeEcommerce \
  --max-files 20 \
  --output ecommerce_analysis.json \
  --batch-size 5

# View summary
python -c "
import json
with open('ecommerce_analysis.json') as f:
    report = json.load(f)
    print(json.dumps(report['summary_metrics'], indent=2))
"
```

### See Also

- `IMPLEMENTATION_GUIDE.md` - Detailed usage and architectural documentation
- `scatter_gather_poc.py` - Main analysis orchestrator
- `repo_ingestion.py` - Repository discovery and file collection
- `results_aggregator.py` - Finding aggregation and reporting

---

**Note**: This is Phase 1 of the multi-phase roadmap. Phase 2 will add metadata extraction and type awareness to reduce false positives.