from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAIError

from repo_ingestion import (
    GitHubRepoFetcher,
    CSFileCollector,
    batch_files,
)
from results_aggregator import (
    ResultsAggregator,
    FileFeedback,
)


GITHUB_MODELS_ENDPOINT = "https://models.github.ai/inference"
DEFAULT_MODEL = "gpt-4.1"


logger = logging.getLogger(__name__)
FAILED_FEEDBACK_PREFIX = "No findings returned because this expert failed"
SKILLS_DIR = Path(__file__).resolve().parent / "Skills"


@dataclass(frozen=True)
class ExpertAgent:
    name: str
    instructions: str


@dataclass(frozen=True)
class ExpertSkill:
    name: str
    skill_file: str


EXPERT_SKILLS: tuple[ExpertSkill, ...] = (
    ExpertSkill(
        name="AsyncExpert",
        skill_file="dotnet-async-expert.md",
    ),
    ExpertSkill(
        name="MemoryExpert",
        skill_file="dotnet-memory-expert.md",
    ),
    ExpertSkill(
        name="ParallelExpert",
        skill_file="dotnet-parallel-expert.md",
    ),
)


REFACTOR_INSTRUCTIONS = (
    "You are a Lead Software Engineer. You will receive the original C# code "
    "followed by combined feedback from specialist reviewers. "
    "Produce only the final, fully corrected C# code that fixes every identified issue."
)


# Default test code (used if no repository provided)
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


def _require_token() -> str:
    load_dotenv()
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN environment variable is not set.")
    return token


def _strip_frontmatter(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    if len(lines) >= 3 and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1:]).strip()
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


async def run_agent(
    client: AsyncOpenAI,
    *,
    model: str,
    name: str,
    instructions: str,
    user_prompt: str,
) -> str:
    try:
        response = await client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_prompt},
            ],
        )
    except OpenAIError as exc:
        raise RuntimeError(f"{name} failed to generate a response.") from exc

    content = response.choices[0].message.content
    return content.strip() if content else ""


async def gather_feedback(
    client: AsyncOpenAI,
    model: str,
    source_code: str,
    experts: tuple[ExpertAgent, ...],
) -> dict[str, str]:
    review_prompt = (
        "Review the following C# code and report your findings:\n\n"
        f"```csharp\n{source_code}\n```"
    )

    tasks = [
        run_agent(
            client,
            model=model,
            name=expert.name,
            instructions=expert.instructions,
            user_prompt=review_prompt,
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
            "All experts failed to respond; continuing with empty expert feedback for refactoring."
        )

    return feedback_by_agent


def build_combined_feedback(feedback_by_agent: dict[str, str]) -> str:
    sections = []
    for agent_name, feedback in feedback_by_agent.items():
        sections.append(f"### {agent_name}\n{feedback}\n")
    return "\n".join(sections).strip()


async def refactor_code(
    client: AsyncOpenAI,
    *,
    model: str,
    source_code: str,
    combined_feedback: str,
) -> str:
    refactor_prompt = f"""
Original code:

```csharp
{source_code}
```

Expert feedback:

{combined_feedback}

Please produce the final, refactored C# code that fixes all issues identified above.
""".strip()

    final_code = await run_agent(
        client,
        model=model,
        name="RefactorAgent",
        instructions=REFACTOR_INSTRUCTIONS,
        user_prompt=refactor_prompt,
    )
    return final_code


async def analyze_file(
    client: AsyncOpenAI,
    *,
    model: str,
    file_path: Path,
    repo_root: Path,
    experts: tuple[ExpertAgent, ...],
) -> FileFeedback:
    """
    Analyze a single C# file using all experts.

    Args:
        client: AsyncOpenAI client
        model: Model to use
        file_path: Path to C# file to analyze
        repo_root: Repository root (for relative paths in context)
        experts: Tuple of expert agents

    Returns:
        FileFeedback with aggregated findings
    """
    try:
        source_code = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning(f"Could not read {file_path} as UTF-8; skipping")
        source_code = f"[File unreadable: {file_path.name}]"

    # Truncate large files
    if len(source_code) > 15000:
        logger.warning(
            f"{file_path.name} is large ({len(source_code)} chars); truncating to 15000")
        source_code = source_code[:15000] + "\n// ... [truncated] ..."

    review_prompt = (
        f"File: {file_path.relative_to(repo_root)}\n\n"
        "Review the following C# code and report your findings:\n\n"
        f"```csharp\n{source_code}\n```"
    )

    tasks = [
        run_agent(
            client,
            model=model,
            name=expert.name,
            instructions=expert.instructions,
            user_prompt=review_prompt,
        )
        for expert in experts
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    feedback_by_agent: dict[str, str] = {}
    for expert, result in zip(experts, results):
        if isinstance(result, Exception):
            logger.error(f"{expert.name} failed for {file_path}: {result}")
            feedback_by_agent[expert.name] = "No findings returned because this expert failed to respond."
            continue
        feedback_by_agent[expert.name] = result or "No findings returned."

    return ResultsAggregator.create_file_feedback(file_path, repo_root, feedback_by_agent)


async def analyze_batch(
    client: AsyncOpenAI,
    *,
    model: str,
    batch_files_list: list[Path],
    repo_root: Path,
    experts: tuple[ExpertAgent, ...],
) -> list[FileFeedback]:
    """
    Analyze a batch of files in parallel.

    Args:
        client: AsyncOpenAI client
        model: Model to use
        batch_files_list: List of file paths in this batch
        repo_root: Repository root
        experts: Tuple of expert agents

    Returns:
        List of FileFeedback objects
    """
    tasks = [
        analyze_file(
            client,
            model=model,
            file_path=file_path,
            repo_root=repo_root,
            experts=experts,
        )
        for file_path in batch_files_list
    ]
    return await asyncio.gather(*tasks, return_exceptions=False)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scatter-Gather POC: Multi-expert code analysis for .NET projects"
    )
    parser.add_argument(
        "--repo",
        type=str,
        help='GitHub repo in format "owner/repo" (e.g., "dotnet/runtime")',
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
        "--local",
        type=Path,
        help="Path to local .NET repository instead of cloning",
        default=Path(".")
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

    args = parser.parse_args()

    github_token = args.token or os.getenv("GITHUB_TOKEN")
    model = os.getenv("GITHUB_MODEL", DEFAULT_MODEL)

    client = AsyncOpenAI(
        api_key=github_token or "sk-placeholder", base_url=GITHUB_MODELS_ENDPOINT)
    experts = load_expert_agents()

    # Determine analysis source
    if args.repo:
        # Clone from GitHub
        owner, repo = args.repo.split("/")
        repo_path = GitHubRepoFetcher.clone_repo(
            owner=owner,
            repo=repo,
            branch=args.branch,
            token=github_token,
        )
        repo_root = repo_path
        repository_name = f"{owner}/{repo}"
    elif args.local:
        # Use local path
        repo_root = args.local
        repository_name = repo_root.name

    # Collect C# files
    cs_files = CSFileCollector.collect(repo_root)

    # Prioritize hot paths (Controllers, Services, Handlers)
    if not args.no_hot_path_only:
        cs_files = CSFileCollector.prioritize_hot_paths(cs_files)

    # Limit to max_files if specified
    if args.max_files:
        cs_files = cs_files[: args.max_files]

    print(f"\n{'='*70}")
    print(f"ANALYSIS SCOPE")
    print(f"{'='*70}")
    print(f"Repository: {repository_name}")
    print(f"Root: {repo_root}")
    print(f"Total C# files found: {len(cs_files)}")
    print(
        f"Files to analyze: {min(len(cs_files), args.max_files or len(cs_files))}")
    print(f"Experts: {len(experts)} ({', '.join(e.name for e in experts)})")
    print(f"Output: {args.output}")
    print(f"{'='*70}\n")

    # Create batches
    file_batches = batch_files(cs_files, batch_size=args.batch_size)

    all_file_feedbacks: list[FileFeedback] = []
    batch_num = 0

    for batch_num, batch in enumerate(file_batches, 1):
        print(
            f"\n[Batch {batch_num}/{len(file_batches)}] Analyzing {len(batch)} files...")

        batch_feedbacks = await analyze_batch(
            client,
            model=model,
            batch_files_list=batch,
            repo_root=repo_root,
            experts=experts,
        )

        all_file_feedbacks.extend(batch_feedbacks)

        # Print quick summary
        total_findings = sum(len(fb.findings) for fb in batch_feedbacks)
        critical_count = sum(
            1 for fb in batch_feedbacks
            for f in fb.findings
            if f.severity == "critical"
        )
        print(
            f"  ✓ Batch complete: {total_findings} findings ({critical_count} critical)")

    # Aggregate results
    print(f"\n\nAggregating findings from {len(all_file_feedbacks)} files...")
    report = ResultsAggregator.aggregate_findings(all_file_feedbacks)
    report.repository_name = repository_name
    report.analyzed_at = datetime.now().isoformat()

    # Save report
    ResultsAggregator.save_report(report, args.output)

    # Print summary
    summary = ResultsAggregator.print_summary(report)
    print("\n" + summary)

    # Print top critical issues
    critical_findings = [
        (fb.relative_path, f)
        for fb in all_file_feedbacks
        for f in fb.findings
        if f.severity == "critical"
    ]

    if critical_findings:
        print("\nTOP CRITICAL ISSUES:")
        print("=" * 70)
        for file_path, finding in critical_findings[:5]:
            print(f"\n{file_path}")
            print(f"  Expert: {finding.expert}")
            print(f"  Issue: {finding.issue}")
            print(f"  Recommendation: {finding.recommendation}")
        if len(critical_findings) > 5:
            print(
                f"\n... and {len(critical_findings) - 5} more critical issues")


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nAnalysis interrupted by user.")
        raise SystemExit(0) from None
    except Exception as exc:
        logger.error(f"Fatal error: {exc}", exc_info=True)
        raise SystemExit(f"Error: {exc}") from exc
