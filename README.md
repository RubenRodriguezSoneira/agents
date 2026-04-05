# agents

## Scatter-Gather POC: Multi-Expert Repository Analysis

Python implementation of a scatter-gather review workflow for .NET repositories.

### Current Capabilities

- Multi-file repository analysis for .cs files.
- Six specialist experts per file:
  - AsyncExpert
  - MemoryExpert
  - ParallelExpert
  - DDDExpert
  - DIExpert
  - LayeringExpert
- Structured JSON finding parsing with plain-text fallback.
- Roslyn-first metadata extraction with heuristic fallback.
- Token-aware batching and request concurrency control.
- Incremental analysis with cache reuse (git + fingerprints).
- Checkpoint persistence and resume support.
- Deduplication of findings within and across merged sources.

### Installation

```bash
pip install -r requirements.txt
```

Environment variables:

- GITHUB_TOKEN: required for non-dry model runs.
- GITHUB_MODEL: optional, default gpt-4.1.

### Quick Start

1. Dry run with built-in default snippet:

```bash
python scatter_gather_poc.py --dry-run
```

2. Analyze a local repository:

```bash
python scatter_gather_poc.py --local C:\path\to\repo --max-files 20
```

3. Analyze a GitHub repository:

```bash
python scatter_gather_poc.py --repo dotnet/runtime --max-files 10
```

4. Resume interrupted analysis:

```bash
python scatter_gather_poc.py --local C:\path\to\repo --resume
```

### CLI Flags

```text
--repo REPO                          GitHub repository in owner/repo format
--local LOCAL                        Local repository path
--branch BRANCH                      Git branch (default: main)
--token TOKEN                        GitHub token override
--output OUTPUT                      Output report path (default: analysis_report.json)
--batch-size BATCH_SIZE              Files per batch (default: 5)
--max-files MAX_FILES                Limit files analyzed
--no-hot-path-only                   Analyze all files (disable hot-path prioritization)
--max-tokens-per-batch N             Token-cap batching (chars/4 estimate)
--max-concurrency N                  Max concurrent model requests (default: 2)
--max-requests-per-minute N          Global pacing limit for model requests (default: 12)
--max-retries N                      OpenAI client internal retry attempts (default: 5)
--max-rate-limit-retries N           Additional retries after HTTP 429 (default: 3)
--cache-dir PATH                     State/checkpoint directory (default: .sg_cache)
--resume                             Resume from checkpoint
--dry-run                            Show projected batches and calls only
--roslyn-timeout SECONDS             Roslyn metadata timeout (default: 300)
```

### Rate Limiting & Concurrency

The system handles HTTP 429 (Too Many Requests) errors with automatic retry logic:

- **Concurrency Control**: `--max-concurrency` controls simultaneous in-flight requests
- **Global Pacing**: `--max-requests-per-minute` enforces a global request interval across all experts/files
- **Built-in Retries**: `--max-retries` controls SDK-level retries for transient API failures
- **Extra 429 Retries**: `--max-rate-limit-retries` adds outer retries after SDK retries are exhausted

If you're still hitting rate limits after retries:
1. Reduce `--max-requests-per-minute` first (for example, 6-10)
2. Reduce `--max-concurrency` to 1-2
3. Increase `--max-rate-limit-retries` if you can tolerate longer runs
4. Use `--batch-size 1` for the most conservative request pattern

### Metadata Extraction

Preferred path:

- Roslyn helper project in RoslynMetadataExtractor via dotnet run.

Fallback path:

- Heuristic extraction in metadata_enricher.py (namespace, type, inheritance, constructor deps, attributes, inferred layer).

Report scope includes metadata_extraction_status with values roslyn, heuristic, or none.

### Incremental + Resume Workflow

- State file stores config signature, commit baseline, fingerprints, and cached FileFeedback values.
- On rerun, unchanged files are reused from cache.
- Resume mode writes checkpoint progress per batch and skips already completed files.
- Checkpoint is cleared on successful completion.

### Report Schema Highlights

Top-level:

- schema_version: 2
- repository_name
- analyzed_at
- scope
- summary_metrics
- file_feedbacks

Additional summary metrics:

- cache_hits
- resumed_count
- files_analyzed_fresh
- files_reused

### Project Structure

```text
.
|-- scatter_gather_poc.py
|-- repo_ingestion.py
|-- results_aggregator.py
|-- metadata_enricher.py
|-- analysis_state.py
|-- RoslynMetadataExtractor/
|   |-- RoslynMetadataExtractor.csproj
|   `-- Program.cs
|-- Skills/
|   |-- dotnet-async-expert.md
|   |-- dotnet-memory-expert.md
|   |-- dotnet-parallel-expert.md
|   |-- dotnet-ddd-expert.md
|   |-- dotnet-di-expert.md
|   `-- dotnet-layering-expert.md
`-- tests/
```

### Validation

```bash
python -m pytest tests -q
```

All current tests pass and cover parsing, dedup, heuristic metadata fallback, and state/checkpoint flows.