import json
from pathlib import Path

from results_aggregator import ResultsAggregator


def _make_cs_file(repo_root: Path, relative_path: str) -> Path:
    file_path = repo_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("public class Demo {}", encoding="utf-8")
    return file_path


def test_create_file_feedback_parses_json_findings(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    file_path = _make_cs_file(repo_root, "src/OrderService.cs")

    feedback_by_agent = {
        "AsyncExpert": json.dumps(
            [
                {
                    "issue": "Potential deadlock due to .Result on async call",
                    "severity": "CRITICAL",
                    "category": "asynchronous",
                    "recommendation": "Use async/await through the full call chain",
                    "line_range": [10, 12],
                },
                {
                    "issue": "Unawaited Task returned from repository method",
                    "severity": "low",
                    "category": "async",
                    "recommendation": "Await the task and propagate cancellation token",
                    "line_range": [23, 24],
                },
            ]
        ),
        "MemoryExpert": "MEMORY: Clean.",
    }

    feedback = ResultsAggregator.create_file_feedback(file_path, repo_root, feedback_by_agent)

    assert len(feedback.findings) == 2
    assert [finding.severity for finding in feedback.findings] == ["critical", "low"]
    assert {finding.category for finding in feedback.findings} == {"async"}
    assert "AsyncExpert" in feedback.expert_summaries
    assert "MemoryExpert" not in feedback.expert_summaries


def test_create_file_feedback_parses_fenced_json_and_normalizes_di(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    file_path = _make_cs_file(repo_root, "src/Startup.cs")

    feedback_by_agent = {
        "DIExpert": """```json
[
  {
    \"issue\": \"Service lifetime mismatch between singleton and scoped dependency\",
    \"severity\": \"warning\",
    \"category\": \"dependency injection\",
    \"recommendation\": \"Align service lifetimes or inject a factory\",
    \"line_range\": [42, 46]
  }
]
```"""
    }

    feedback = ResultsAggregator.create_file_feedback(file_path, repo_root, feedback_by_agent)

    assert len(feedback.findings) == 1
    finding = feedback.findings[0]
    assert finding.category == "di"
    assert finding.severity == "high"


def test_create_file_feedback_uses_plain_text_fallback(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    file_path = _make_cs_file(repo_root, "src/ParallelProcessor.cs")

    feedback_by_agent = {
        "ParallelExpert": (
            "Issue: Race condition in Parallel.ForEach mutating shared List<int>\n"
            "Recommendation: Use ConcurrentBag<int> or synchronize writes with lock"
        ),
        "AsyncExpert": "No findings returned because this expert failed to respond.",
    }

    feedback = ResultsAggregator.create_file_feedback(file_path, repo_root, feedback_by_agent)

    assert len(feedback.findings) == 1
    finding = feedback.findings[0]
    assert finding.category == "parallel"
    assert finding.severity == "critical"
    assert "Race condition" in finding.issue
    assert finding.recommendation.startswith("Use ConcurrentBag")
    assert set(feedback.expert_summaries.keys()) == {"ParallelExpert"}
