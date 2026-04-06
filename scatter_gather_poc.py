from __future__ import annotations

import argparse
import asyncio
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAIError, RateLimitError

from analysis_state import AnalysisStateManager, make_config_signature
from metadata_enricher import build_metadata_context, extract_repository_metadata
from repo_ingestion import CSFileCollector, GitHubRepoFetcher, batch_files
from results_aggregator import FileFeedback, ResultsAggregator


GITHUB_MODELS_ENDPOINT = "https://models.github.ai/inference"
OLLAMA_DEFAULT_ENDPOINT = "http://localhost:11434/v1"
PROVIDER_GITHUB = "github"
PROVIDER_OLLAMA = "ollama"
DEFAULT_MODEL = "gpt-4.1"
DEFAULT_OLLAMA_MODEL = "qwen3-coder-next"
MAX_SOURCE_CHARS = 15000
DEFAULT_MAX_CONCURRENCY = 2
DEFAULT_MAX_REQUESTS_PER_MINUTE = 12
DEFAULT_ROSLYN_TIMEOUT = 300
MAX_RETRIES = 5
DEFAULT_MAX_RATE_LIMIT_RETRIES = 3
INITIAL_RETRY_BACKOFF_SECS = 2
MAX_RETRY_BACKOFF_SECS = 60

logger = logging.getLogger(__name__)
FAILED_FEEDBACK_PREFIX = "No findings returned because this expert failed"
SKILLS_DIR = Path(__file__).resolve().parent / "Skills"
ROSLYN_EXTRACTOR_DIR = Path(
    __file__).resolve().parent / "RoslynMetadataExtractor"


@dataclass(frozen=True)
class ExpertAgent:
    name: str
    instructions: str


@dataclass(frozen=True)
class ExpertSkill:
    name: str
    skill_file: str


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    base_url: str


EXPERT_SKILLS: tuple[ExpertSkill, ...] = (
    ExpertSkill(name="AsyncExpert", skill_file="dotnet-async-expert.md"),
    ExpertSkill(name="MemoryExpert", skill_file="dotnet-memory-expert.md"),
    ExpertSkill(name="ParallelExpert", skill_file="dotnet-parallel-expert.md"),
    ExpertSkill(name="DDDExpert", skill_file="dotnet-ddd-expert.md"),
    ExpertSkill(name="DIExpert", skill_file="dotnet-di-expert.md"),
    ExpertSkill(name="LayeringExpert", skill_file="dotnet-layering-expert.md"),
)


# Used when no repository source is provided.
DEFAULT_MESSY_CODE = """
public static List<int> ProcessData(string url)
{
    // Async deadlock: .Result blocks the calling thread
    var httpClient = new HttpClient();
    var response = httpClient.GetAsync(url).Result;
    var content = response.Content.ReadAsStringAsync().Result;

    // Memory leak: MemoryStream is created but never disposed
    var stream = new MemoryStream(System.Text.Encoding.UTF8.GetBytes(content));
    int length = (int)stream.Length;

    // Race condition: List<int> is not thread-safe for concurrent writes
    var results = new List<int>();
    Parallel.ForEach(Enumerable.Range(0, length), i =>
    {
        results.Add(i * 2);
    });

    return results;
}
""".strip()


@dataclass
class AnalysisPlan:
    files_requiring_analysis: list[Path]
    reused_feedbacks: list[FileFeedback]
    resumed_feedbacks: list[FileFeedback]
    checkpoint_completed: dict[str, FileFeedback]
    current_fingerprints: dict[str, str]
    cache_hits: int
    resumed_count: int


class RequestRateLimiter:
    """Simple global request pacer using a fixed minimum interval."""

    def __init__(self, requests_per_minute: int) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be greater than 0")

        self._interval_seconds = 60.0 / float(requests_per_minute)
        self._next_available_at = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_for = self._next_available_at - now
            if wait_for > 0:
                await asyncio.sleep(wait_for)
                now = time.monotonic()

            self._next_available_at = max(
                self._next_available_at, now) + self._interval_seconds

    async def penalize(self, seconds: float) -> None:
        if seconds <= 0:
            return

        async with self._lock:
            self._next_available_at = max(
                self._next_available_at,
                time.monotonic() + seconds,
            )


def _retry_after_seconds(exc: RateLimitError) -> Optional[float]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None

    retry_after = headers.get("retry-after")
    if retry_after is None:
        return None

    try:
        return max(0.0, float(retry_after))
    except (TypeError, ValueError):
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scatter-Gather POC: Multi-expert code analysis for .NET projects"
    )

    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--repo",
        type=str,
        help='GitHub repo in format "owner/repo" (example: "dotnet/runtime")',
    )
    source_group.add_argument(
        "--local",
        type=Path,
        help="Path to local .NET repository instead of cloning",
    )

    parser.add_argument(
        "--branch",
        type=str,
        default="main",
        help="Git branch to analyze (default: main)",
    )
    parser.add_argument(
        "--token",
        type=str,
        help="GitHub token for private repos (or use GITHUB_TOKEN env var)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("analysis_report.json"),
        help="Output file for analysis report (default: analysis_report.json)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Number of files per batch (default: 5)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Limit analysis to first N files (useful for testing)",
    )
    parser.add_argument(
        "--no-hot-path-only",
        action="store_true",
        help="Analyze all files (not just hot paths like Controllers/Services)",
    )
    parser.add_argument(
        "--max-tokens-per-batch",
        type=int,
        default=None,
        help="Cap estimated input tokens per batch (chars/4 heuristic)",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=DEFAULT_MAX_CONCURRENCY,
        help="Max concurrent model requests across all experts (default: 2)",
    )
    parser.add_argument(
        "--max-requests-per-minute",
        type=int,
        default=DEFAULT_MAX_REQUESTS_PER_MINUTE,
        help="Global pacing limit for outbound model requests (default: 12)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_RETRIES,
        help="Model client internal retry attempts for transient failures (default: 5)",
    )
    parser.add_argument(
        "--provider",
        choices=[PROVIDER_OLLAMA, PROVIDER_GITHUB],
        default=None,
        help="Model provider (github or ollama). Defaults to MODEL_PROVIDER env or github.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=(
            "Model name override. Defaults to GITHUB_MODEL (provider=github) "
            "or OLLAMA_MODEL (provider=ollama)."
        ),
    )
    parser.add_argument(
        "--ollama-base-url",
        type=str,
        default=None,
        help=(
            "Ollama OpenAI-compatible endpoint (default: OLLAMA_BASE_URL env "
            "or http://localhost:11434/v1). If /v1 is missing, it is added automatically."
        ),
    )
    parser.add_argument(
        "--max-rate-limit-retries",
        type=int,
        default=DEFAULT_MAX_RATE_LIMIT_RETRIES,
        help="Additional retries after 429 responses with exponential backoff (default: 3)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".sg_cache"),
        help="Directory for incremental analysis state and checkpoints",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from a saved checkpoint for unfinished runs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report projected batches and API calls without model requests",
    )
    parser.add_argument(
        "--roslyn-timeout",
        type=int,
        default=DEFAULT_ROSLYN_TIMEOUT,
        help="Timeout in seconds for Roslyn metadata extraction (default: 300)",
    )

    return parser.parse_args()


def _require_token(cli_token: str | None) -> str:
    load_dotenv()
    token = cli_token or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN environment variable is not set.")
    return token


def _build_model_config(args: argparse.Namespace) -> ModelConfig:
    provider = (args.provider or os.getenv("MODEL_PROVIDER")
                or PROVIDER_GITHUB).strip().lower()
    if provider not in {PROVIDER_GITHUB, PROVIDER_OLLAMA}:
        raise ValueError(
            f"Unsupported provider: {provider!r}. Choose '{PROVIDER_GITHUB}' or '{PROVIDER_OLLAMA}'."
        )

    if provider == PROVIDER_OLLAMA:
        model = (args.model or os.getenv("OLLAMA_MODEL")
                 or DEFAULT_OLLAMA_MODEL).strip()
        raw_base_url = (
            args.ollama_base_url
            or os.getenv("OLLAMA_BASE_URL")
            or OLLAMA_DEFAULT_ENDPOINT
        )
        base_url = raw_base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
            logger.info(
                "Normalized Ollama base URL from '%s' to '%s' for OpenAI-compatible API.",
                raw_base_url,
                base_url,
            )
    else:
        model = (args.model or os.getenv(
            "GITHUB_MODEL") or DEFAULT_MODEL).strip()
        base_url = GITHUB_MODELS_ENDPOINT

    if not model:
        raise ValueError("Model name cannot be empty.")

    return ModelConfig(provider=provider, model=model, base_url=base_url)


def _normalize_model_name(name: str) -> str:
    return name.strip().lower()


def _model_name_matches(requested: str, available: str) -> bool:
    req = _normalize_model_name(requested)
    avail = _normalize_model_name(available)
    if req == avail:
        return True

    # Treat explicit and implicit latest tags as equivalent.
    if req.endswith(":latest"):
        return req[:-7] == avail
    if avail.endswith(":latest"):
        return req == avail[:-7]

    return False


async def _validate_ollama_model(client: AsyncOpenAI, model: str) -> None:
    """Fail fast when selected Ollama model is not available locally."""
    try:
        models_response = await client.models.list()
    except OpenAIError as exc:
        raise RuntimeError(
            "Failed to query Ollama models from the OpenAI-compatible API. "
            "Confirm Ollama is running and reachable at the configured endpoint."
        ) from exc

    available_models = [
        item.id
        for item in models_response.data
        if getattr(item, "id", None)
    ]

    if not available_models:
        raise RuntimeError(
            "No Ollama models were reported by /v1/models. "
            "Pull a model first and rerun."
        )

    if any(_model_name_matches(model, available) for available in available_models):
        return

    preview = ", ".join(sorted(available_models)[:10])
    raise RuntimeError(
        f"Ollama model '{model}' was not found. Available models: {preview}. "
        "Use --model with one of the available model IDs, or pull the requested model first."
    )


def _normalize_relative_path(path: str | Path) -> str:
    return Path(path).as_posix()


def _resolve_cache_dir(repo_root: Path, cache_dir: Path) -> Path:
    if cache_dir.is_absolute():
        return cache_dir
    return (repo_root / cache_dir).resolve()


def _build_config_signature(args: argparse.Namespace, model: str, experts: tuple[ExpertAgent, ...]) -> str:
    config_payload = {
        "provider": args.provider,
        "model": model,
        "experts": [expert.name for expert in experts],
        "batch_size": args.batch_size,
        "max_tokens_per_batch": args.max_tokens_per_batch,
        "max_concurrency": args.max_concurrency,
        "max_requests_per_minute": args.max_requests_per_minute,
        "max_rate_limit_retries": args.max_rate_limit_retries,
        "hot_path_only": not args.no_hot_path_only,
        "max_files": args.max_files,
        "max_source_chars": MAX_SOURCE_CHARS,
        "schema_version": 2,
    }
    return make_config_signature(config_payload)


def _strip_frontmatter(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    if len(lines) >= 3 and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                return "\n".join(lines[idx + 1:]).strip()
    return markdown_text.strip()


def load_expert_agents(skills_dir: Path = SKILLS_DIR) -> tuple[ExpertAgent, ...]:
    agents: list[ExpertAgent] = []
    for skill in EXPERT_SKILLS:
        skill_path = skills_dir / skill.skill_file
        try:
            raw_markdown = skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"Failed to load skill instructions for {skill.name} from {skill_path}."
            ) from exc

        instructions = _strip_frontmatter(raw_markdown)
        if not instructions:
            raise RuntimeError(
                f"Skill file for {skill.name} is empty after removing frontmatter: {skill_path}."
            )

        agents.append(ExpertAgent(name=skill.name, instructions=instructions))

    return tuple(agents)


def _truncate_source(source_code: str, file_path: Path) -> str:
    if len(source_code) <= MAX_SOURCE_CHARS:
        return source_code

    logger.warning(
        "%s is large (%s chars); truncating to %s chars",
        file_path.name,
        len(source_code),
        MAX_SOURCE_CHARS,
    )
    return source_code[:MAX_SOURCE_CHARS] + "\n// ... [truncated] ..."


async def run_agent(
    client: AsyncOpenAI,
    *,
    model: str,
    name: str,
    instructions: str,
    user_prompt: str,
    rate_limiter: RequestRateLimiter,
    max_rate_limit_retries: int,
) -> str:
    """Run an agent with request pacing and robust 429 retry handling."""
    for attempt in range(max_rate_limit_retries + 1):
        await rate_limiter.acquire()
        try:
            response = await client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content
            return content.strip() if content else ""
        except RateLimitError as exc:
            if attempt >= max_rate_limit_retries:
                raise RuntimeError(
                    f"{name} failed due to repeated rate limiting after "
                    f"{max_rate_limit_retries + 1} attempts."
                ) from exc

            retry_after = _retry_after_seconds(exc)
            computed_backoff = min(
                MAX_RETRY_BACKOFF_SECS,
                float(INITIAL_RETRY_BACKOFF_SECS * (2 ** attempt)),
            )
            wait_seconds = retry_after if retry_after is not None else computed_backoff

            # Add small deterministic jitter to reduce synchronized retries.
            wait_seconds += min(1.0, 0.2 * (attempt + 1))

            logger.warning(
                "%s hit HTTP 429. Waiting %.1fs before retry %d/%d.",
                name,
                wait_seconds,
                attempt + 1,
                max_rate_limit_retries,
            )
            await rate_limiter.penalize(wait_seconds)
            await asyncio.sleep(wait_seconds)
        except OpenAIError as exc:
            logger.exception("%s OpenAI error: %s", name, exc)
            raise RuntimeError(
                f"{name} failed to generate a response: {exc}") from exc


async def run_agent_throttled(
    client: AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    *,
    model: str,
    name: str,
    instructions: str,
    user_prompt: str,
    rate_limiter: RequestRateLimiter,
    max_rate_limit_retries: int,
) -> str:
    async with semaphore:
        return await run_agent(
            client,
            model=model,
            name=name,
            instructions=instructions,
            user_prompt=user_prompt,
            rate_limiter=rate_limiter,
            max_rate_limit_retries=max_rate_limit_retries,
        )


async def gather_feedback(
    client: AsyncOpenAI,
    *,
    semaphore: asyncio.Semaphore,
    model: str,
    source_code: str,
    relative_path: Path,
    metadata_context: str,
    experts: tuple[ExpertAgent, ...],
    rate_limiter: RequestRateLimiter,
    max_rate_limit_retries: int,
) -> dict[str, str]:
    review_prompt = (
        f"File: {relative_path}\n\n"
        "Repository metadata context:\n"
        f"{metadata_context}\n\n"
        "Review the following C# code and report your findings:\n\n"
        f"```csharp\n{source_code}\n```"
    )

    tasks = [
        run_agent_throttled(
            client,
            semaphore,
            model=model,
            name=expert.name,
            instructions=expert.instructions,
            user_prompt=review_prompt,
            rate_limiter=rate_limiter,
            max_rate_limit_retries=max_rate_limit_retries,
        )
        for expert in experts
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    feedback_by_agent: dict[str, str] = {}
    for expert, result in zip(experts, results):
        if isinstance(result, Exception):
            logger.error("%s failed during feedback gathering: %s",
                         expert.name, result)
            feedback_by_agent[expert.name] = (
                "No findings returned because this expert failed to respond."
            )
            continue
        feedback_by_agent[expert.name] = result or "No findings returned."

    if all(
        feedback.startswith(FAILED_FEEDBACK_PREFIX)
        for feedback in feedback_by_agent.values()
    ):
        logger.warning(
            "All experts failed to respond for file: %s", relative_path)

    return feedback_by_agent


async def analyze_file(
    client: AsyncOpenAI,
    *,
    semaphore: asyncio.Semaphore,
    model: str,
    file_path: Path,
    repo_root: Path,
    metadata_bundle: dict,
    experts: tuple[ExpertAgent, ...],
    rate_limiter: RequestRateLimiter,
    max_rate_limit_retries: int,
) -> FileFeedback:
    try:
        source_code = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning(
            "Could not read %s (%s); using placeholder", file_path, exc)
        source_code = f"[File unreadable: {file_path.name}]"

    source_code = _truncate_source(source_code, file_path)

    try:
        relative_path = file_path.relative_to(repo_root)
    except ValueError:
        relative_path = file_path

    metadata_context = build_metadata_context(
        file_path=file_path,
        repo_root=repo_root,
        metadata_bundle=metadata_bundle,
    )

    feedback_by_agent = await gather_feedback(
        client,
        semaphore=semaphore,
        model=model,
        source_code=source_code,
        relative_path=relative_path,
        metadata_context=metadata_context,
        experts=experts,
        rate_limiter=rate_limiter,
        max_rate_limit_retries=max_rate_limit_retries,
    )

    return ResultsAggregator.create_file_feedback(file_path, repo_root, feedback_by_agent)


async def analyze_batch(
    client: AsyncOpenAI,
    *,
    semaphore: asyncio.Semaphore,
    model: str,
    batch_files_list: list[Path],
    repo_root: Path,
    metadata_bundle: dict,
    experts: tuple[ExpertAgent, ...],
    rate_limiter: RequestRateLimiter,
    max_rate_limit_retries: int,
) -> list[FileFeedback]:
    tasks = [
        analyze_file(
            client,
            semaphore=semaphore,
            model=model,
            file_path=file_path,
            repo_root=repo_root,
            metadata_bundle=metadata_bundle,
            experts=experts,
            rate_limiter=rate_limiter,
            max_rate_limit_retries=max_rate_limit_retries,
        )
        for file_path in batch_files_list
    ]
    return await asyncio.gather(*tasks)


def _split_owner_repo(repo: str) -> tuple[str, str]:
    parts = repo.strip().split("/", maxsplit=1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"Invalid --repo format: {repo!r}. Expected 'owner/repo'."
        )
    return parts[0], parts[1]


def _print_scope(
    repository_name: str,
    repo_root: Path,
    total_found: int,
    files_to_analyze: int,
    files_requiring_analysis: int,
    cache_hits: int,
    resumed_count: int,
    metadata_status: str,
    experts: tuple[ExpertAgent, ...],
    max_concurrency: int,
    max_requests_per_minute: int,
    max_rate_limit_retries: int,
    provider: str,
    model: str,
    base_url: str,
    output: Path,
) -> None:
    print(f"\n{'=' * 70}")
    print("ANALYSIS SCOPE")
    print(f"{'=' * 70}")
    print(f"Repository: {repository_name}")
    print(f"Root: {repo_root}")
    print(f"Total C# files found: {total_found}")
    print(f"Files to analyze: {files_to_analyze}")
    print(f"Files requiring fresh analysis: {files_requiring_analysis}")
    print(f"Files reused from cache: {cache_hits}")
    print(f"Files restored from checkpoint: {resumed_count}")
    print(f"Metadata extraction status: {metadata_status}")
    print(f"Experts: {len(experts)} ({', '.join(e.name for e in experts)})")
    print(f"Provider/model: {provider}/{model}")
    print(f"Endpoint: {base_url}")
    print(f"Max concurrency: {max_concurrency}")
    print(f"Max requests per minute: {max_requests_per_minute}")
    print(f"Extra 429 retries: {max_rate_limit_retries}")
    print(f"Output: {output}")
    print(f"{'=' * 70}\n")


def _print_dry_run(
    repository_name: str,
    total_found: int,
    selected_count: int,
    analysis_count: int,
    batch_count: int,
    experts: tuple[ExpertAgent, ...],
    provider: str,
    model: str,
) -> None:
    projected_calls = analysis_count * len(experts)
    print(f"\n{'=' * 70}")
    print("DRY RUN")
    print(f"{'=' * 70}")
    print(f"Repository: {repository_name}")
    print(f"Total C# files found: {total_found}")
    print(f"Files selected for scope: {selected_count}")
    print(f"Files requiring fresh analysis: {analysis_count}")
    print(f"Provider/model: {provider}/{model}")
    print(f"Batches: {batch_count}")
    print(f"Experts: {len(experts)} ({', '.join(e.name for e in experts)})")
    print(f"Projected model calls: {projected_calls}")
    print("No model requests were made.")
    print(f"{'=' * 70}\n")


def _plan_analysis(
    *,
    files_to_analyze: list[Path],
    state_manager: Optional[AnalysisStateManager],
    config_signature: str,
    resume_enabled: bool,
) -> AnalysisPlan:
    if state_manager is None:
        return AnalysisPlan(
            files_requiring_analysis=list(files_to_analyze),
            reused_feedbacks=[],
            resumed_feedbacks=[],
            checkpoint_completed={},
            current_fingerprints={},
            cache_hits=0,
            resumed_count=0,
        )

    state_payload = state_manager.load_state()
    state_matches = state_payload.get("config_signature") == config_signature

    current_fingerprints = state_manager.compute_fingerprints(files_to_analyze)
    previous_fingerprints: dict[str, str] = {}
    cached_feedbacks: dict[str, FileFeedback] = {}
    changed_by_git: Optional[set[str]] = None

    if state_matches:
        raw_previous = state_payload.get("file_fingerprints", {})
        if isinstance(raw_previous, dict):
            previous_fingerprints = {
                _normalize_relative_path(path): str(fp)
                for path, fp in raw_previous.items()
                if isinstance(fp, str)
            }

        cached_feedbacks = state_manager.deserialize_cached_feedbacks(
            state_payload)
        changed_by_git = state_manager.changed_files_from_git(
            state_payload.get("last_analyzed_commit")
        )

    reused_feedbacks: list[FileFeedback] = []
    files_requiring_analysis: list[Path] = []

    for file_path in files_to_analyze:
        relative_path = state_manager.to_relative_path(file_path)
        cached_feedback = cached_feedbacks.get(relative_path)
        current_fingerprint = current_fingerprints.get(relative_path)
        previous_fingerprint = previous_fingerprints.get(relative_path)

        unchanged = (
            cached_feedback is not None
            and current_fingerprint is not None
            and previous_fingerprint is not None
            and current_fingerprint == previous_fingerprint
        )

        if unchanged and changed_by_git is not None and relative_path in changed_by_git:
            unchanged = False

        if unchanged and cached_feedback is not None:
            reused_feedbacks.append(cached_feedback)
        else:
            files_requiring_analysis.append(file_path)

    resumed_feedbacks: list[FileFeedback] = []
    checkpoint_completed: dict[str, FileFeedback] = {}

    if resume_enabled:
        checkpoint_payload = state_manager.load_checkpoint()
        if checkpoint_payload and checkpoint_payload.get("config_signature") == config_signature:
            checkpoint_completed = state_manager.deserialize_checkpoint_feedbacks(
                checkpoint_payload)

            remaining_files: list[Path] = []
            for file_path in files_requiring_analysis:
                relative_path = state_manager.to_relative_path(file_path)
                completed_feedback = checkpoint_completed.get(relative_path)
                if completed_feedback is not None:
                    resumed_feedbacks.append(completed_feedback)
                else:
                    remaining_files.append(file_path)

            files_requiring_analysis = remaining_files

    return AnalysisPlan(
        files_requiring_analysis=files_requiring_analysis,
        reused_feedbacks=reused_feedbacks,
        resumed_feedbacks=resumed_feedbacks,
        checkpoint_completed=checkpoint_completed,
        current_fingerprints=current_fingerprints,
        cache_hits=len(reused_feedbacks),
        resumed_count=len(resumed_feedbacks),
    )


def _print_batch_summary(batch_feedbacks: list[FileFeedback]) -> None:
    total_findings = sum(len(fb.findings) for fb in batch_feedbacks)
    critical_count = sum(
        1 for fb in batch_feedbacks for finding in fb.findings if finding.severity == "critical"
    )
    print(
        f"  Batch complete: {total_findings} findings ({critical_count} critical)")


def _print_top_critical_issues(file_feedbacks: list[FileFeedback], limit: int = 5) -> None:
    critical_findings = [
        (feedback.relative_path, finding)
        for feedback in file_feedbacks
        for finding in feedback.findings
        if finding.severity == "critical"
    ]

    if not critical_findings:
        return

    print("\nTOP CRITICAL ISSUES:")
    print("=" * 70)
    for relative_path, finding in critical_findings[:limit]:
        print(f"\n{relative_path}")
        print(f"  Expert: {finding.expert}")
        print(f"  Issue: {finding.issue}")
        print(f"  Recommendation: {finding.recommendation}")

    remaining = len(critical_findings) - limit
    if remaining > 0:
        print(f"\n... and {remaining} more critical issues")


async def main() -> None:
    args = parse_args()
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0.")
    if args.max_concurrency <= 0:
        raise ValueError("--max-concurrency must be greater than 0.")
    if args.max_requests_per_minute <= 0:
        raise ValueError("--max-requests-per-minute must be greater than 0.")
    if args.max_rate_limit_retries < 0:
        raise ValueError("--max-rate-limit-retries cannot be negative.")
    if args.max_tokens_per_batch is not None and args.max_tokens_per_batch <= 0:
        raise ValueError(
            "--max-tokens-per-batch must be greater than 0 when provided.")
    if args.roslyn_timeout <= 0:
        raise ValueError("--roslyn-timeout must be greater than 0.")

    load_dotenv()
    github_token = args.token or os.getenv("GITHUB_TOKEN")
    model_config = _build_model_config(args)
    args.provider = model_config.provider
    model = model_config.model
    experts = load_expert_agents()
    config_signature = _build_config_signature(
        args, model=model, experts=experts)

    temp_context: Optional[tempfile.TemporaryDirectory] = None
    client: Optional[AsyncOpenAI] = None

    try:
        source_mode = "default"
        if args.repo:
            owner, repo = _split_owner_repo(args.repo)
            repo_root = GitHubRepoFetcher.clone_repo(
                owner=owner,
                repo=repo,
                branch=args.branch,
                token=github_token,
            )
            repository_name = f"{owner}/{repo}"
            all_cs_files = CSFileCollector.collect(repo_root)
            source_mode = "repo"
        elif args.local:
            repo_root = GitHubRepoFetcher.from_local_path(args.local)
            repository_name = repo_root.name
            all_cs_files = CSFileCollector.collect(repo_root)
            source_mode = "local"
        else:
            temp_context = tempfile.TemporaryDirectory(
                prefix="scatter_gather_default_")
            repo_root = Path(temp_context.name)
            test_file = repo_root / "DefaultSnippet.cs"
            test_file.write_text(DEFAULT_MESSY_CODE, encoding="utf-8")
            repository_name = "default-test-code"
            all_cs_files = [test_file]

        files_to_analyze = list(all_cs_files)
        if not args.no_hot_path_only and (args.repo or args.local):
            files_to_analyze = CSFileCollector.prioritize_hot_paths(
                files_to_analyze)

        if args.max_files is not None:
            files_to_analyze = files_to_analyze[: args.max_files]

        if not files_to_analyze:
            raise RuntimeError(
                "No C# files found to analyze with the selected options.")

        state_manager: Optional[AnalysisStateManager] = None
        if source_mode in {"repo", "local"}:
            state_manager = AnalysisStateManager(
                repo_root=repo_root,
                cache_dir=_resolve_cache_dir(repo_root, args.cache_dir),
            )

        plan = _plan_analysis(
            files_to_analyze=files_to_analyze,
            state_manager=state_manager,
            config_signature=config_signature,
            resume_enabled=args.resume,
        )

        batches_to_run = batch_files(
            plan.files_requiring_analysis,
            batch_size=args.batch_size,
            max_tokens_per_batch=args.max_tokens_per_batch,
        )

        if args.dry_run:
            _print_dry_run(
                repository_name=repository_name,
                total_found=len(all_cs_files),
                selected_count=len(files_to_analyze),
                analysis_count=len(plan.files_requiring_analysis),
                batch_count=len(batches_to_run),
                experts=experts,
                provider=model_config.provider,
                model=model,
            )
            return

        if model_config.provider == PROVIDER_OLLAMA:
            model_api_key = os.getenv("OLLAMA_API_KEY", "ollama")
        else:
            model_api_key = _require_token(args.token)

        client = AsyncOpenAI(
            api_key=model_api_key,
            base_url=model_config.base_url,
            max_retries=args.max_retries,
        )

        if model_config.provider == PROVIDER_OLLAMA:
            await _validate_ollama_model(client, model)

        metadata_bundle = extract_repository_metadata(
            repo_root=repo_root,
            files=files_to_analyze,
            extractor_project=ROSLYN_EXTRACTOR_DIR,
            timeout_seconds=args.roslyn_timeout,
        )
        metadata_status = str(metadata_bundle.get(
            "metadata_extraction_status") or "none")

        _print_scope(
            repository_name=repository_name,
            repo_root=repo_root,
            total_found=len(all_cs_files),
            files_to_analyze=len(files_to_analyze),
            files_requiring_analysis=len(plan.files_requiring_analysis),
            cache_hits=plan.cache_hits,
            resumed_count=plan.resumed_count,
            metadata_status=metadata_status,
            experts=experts,
            max_concurrency=args.max_concurrency,
            max_requests_per_minute=args.max_requests_per_minute,
            max_rate_limit_retries=args.max_rate_limit_retries,
            provider=model_config.provider,
            model=model,
            base_url=model_config.base_url,
            output=args.output,
        )

        semaphore = asyncio.Semaphore(args.max_concurrency)
        rate_limiter = RequestRateLimiter(args.max_requests_per_minute)

        all_file_feedbacks: list[FileFeedback] = []
        all_file_feedbacks.extend(plan.reused_feedbacks)
        all_file_feedbacks.extend(plan.resumed_feedbacks)

        checkpoint_completed = dict(plan.checkpoint_completed)
        pending_paths = {
            state_manager.to_relative_path(file_path): file_path
            for file_path in plan.files_requiring_analysis
        } if state_manager is not None else {}

        fresh_feedbacks: list[FileFeedback] = []

        for batch_index, file_batch in enumerate(batches_to_run, start=1):
            print(
                f"[Batch {batch_index}/{len(batches_to_run)}] "
                f"Analyzing {len(file_batch)} files..."
            )
            batch_feedbacks = await analyze_batch(
                client,
                semaphore=semaphore,
                model=model,
                batch_files_list=file_batch,
                repo_root=repo_root,
                metadata_bundle=metadata_bundle,
                experts=experts,
                rate_limiter=rate_limiter,
                max_rate_limit_retries=args.max_rate_limit_retries,
            )

            fresh_feedbacks.extend(batch_feedbacks)
            all_file_feedbacks.extend(batch_feedbacks)
            _print_batch_summary(batch_feedbacks)

            if args.resume and state_manager is not None:
                for feedback in batch_feedbacks:
                    relative_path = _normalize_relative_path(
                        feedback.relative_path)
                    checkpoint_completed[relative_path] = feedback
                    pending_paths.pop(relative_path, None)

                state_manager.save_checkpoint(
                    config_signature=config_signature,
                    completed_feedbacks=checkpoint_completed,
                    pending_paths=sorted(pending_paths.keys()),
                )

        print(
            f"\nAggregating findings from {len(all_file_feedbacks)} files...")

        merged_feedbacks = ResultsAggregator.merge_feedbacks(
            all_file_feedbacks)
        report = ResultsAggregator.aggregate_findings(merged_feedbacks)
        report.repository_name = repository_name
        report.analyzed_at = datetime.now().isoformat()
        report.scope = {
            "repository": repository_name,
            "root": str(repo_root),
            "total_files_found": len(all_cs_files),
            "files_analyzed": len(files_to_analyze),
            "batch_size": args.batch_size,
            "max_tokens_per_batch": args.max_tokens_per_batch,
            "max_concurrency": args.max_concurrency,
            "max_requests_per_minute": args.max_requests_per_minute,
            "max_rate_limit_retries": args.max_rate_limit_retries,
            "provider": model_config.provider,
            "model": model,
            "base_url": model_config.base_url,
            "hot_path_only": not args.no_hot_path_only,
            "branch": args.branch,
            "cache_dir": str(state_manager.cache_dir) if state_manager is not None else None,
            "resume_enabled": args.resume,
            "metadata_extraction_status": metadata_status,
            "metadata_errors": metadata_bundle.get("errors", []),
            "config_signature": config_signature,
        }

        report.summary_metrics.update(
            {
                "cache_hits": plan.cache_hits,
                "resumed_count": plan.resumed_count,
                "files_analyzed_fresh": len(fresh_feedbacks),
                "files_reused": plan.cache_hits + plan.resumed_count,
            }
        )

        ResultsAggregator.save_report(report, args.output)
        print("\n" + ResultsAggregator.print_summary(report))
        _print_top_critical_issues(report.file_feedbacks)

        if state_manager is not None:
            cached_feedbacks = {
                _normalize_relative_path(feedback.relative_path): feedback
                for feedback in report.file_feedbacks
            }
            state_manager.save_state(
                config_signature=config_signature,
                last_analyzed_commit=state_manager.current_commit_sha(),
                file_fingerprints=plan.current_fingerprints,
                cached_feedbacks=cached_feedbacks,
            )

            if args.resume:
                state_manager.clear_checkpoint()
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to close AsyncOpenAI client cleanly: %s", exc)

        if temp_context is not None:
            temp_context.cleanup()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAnalysis interrupted by user.")
        raise SystemExit(0) from None
    except Exception as exc:
        logger.error("Fatal error: %s", exc, exc_info=True)
        raise SystemExit(f"Error: {exc}") from exc
