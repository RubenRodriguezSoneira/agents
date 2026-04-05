from __future__ import annotations

from pathlib import Path

from analysis_state import AnalysisStateManager, make_config_signature
from results_aggregator import FileFeedback, Finding


def _feedback(relative_path: str, issue: str) -> FileFeedback:
    return FileFeedback(
        file_path=relative_path,
        relative_path=relative_path,
        findings=[
            Finding(
                expert="AsyncExpert",
                issue=issue,
                severity="high",
                recommendation="Use async/await",
                category="async",
            )
        ],
        expert_summaries={"AsyncExpert": issue},
    )


def test_state_roundtrip_and_fingerprint_invalidation(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    cache_dir = tmp_path / "cache"
    source_file = repo_root / "src" / "Service.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("public class Service {}", encoding="utf-8")

    manager = AnalysisStateManager(repo_root=repo_root, cache_dir=cache_dir)

    config_signature = make_config_signature({"model": "test", "experts": ["AsyncExpert"]})
    fingerprints_before = manager.compute_fingerprints([source_file])

    manager.save_state(
        config_signature=config_signature,
        last_analyzed_commit="abc123",
        file_fingerprints=fingerprints_before,
        cached_feedbacks={"src/Service.cs": _feedback("src/Service.cs", "Issue one")},
    )

    loaded_state = manager.load_state()
    assert loaded_state["config_signature"] == config_signature

    loaded_feedbacks = manager.deserialize_cached_feedbacks(loaded_state)
    assert "src/Service.cs" in loaded_feedbacks
    assert loaded_feedbacks["src/Service.cs"].findings[0].issue == "Issue one"

    source_file.write_text("public class Service { public int X => 1; }", encoding="utf-8")
    fingerprints_after = manager.compute_fingerprints([source_file])

    assert fingerprints_before["src/Service.cs"] != fingerprints_after["src/Service.cs"]


def test_checkpoint_roundtrip_and_clear(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    cache_dir = tmp_path / "cache"
    repo_root.mkdir(parents=True, exist_ok=True)

    manager = AnalysisStateManager(repo_root=repo_root, cache_dir=cache_dir)

    config_signature = make_config_signature({"resume": True, "batch_size": 5})
    completed = {
        "src/OrderService.cs": _feedback(
            "src/OrderService.cs", "Potential deadlock in .Result usage"
        )
    }

    manager.save_checkpoint(
        config_signature=config_signature,
        completed_feedbacks=completed,
        pending_paths=["src/Other.cs"],
    )

    payload = manager.load_checkpoint()
    assert payload is not None
    assert payload["config_signature"] == config_signature

    restored = manager.deserialize_checkpoint_feedbacks(payload)
    assert "src/OrderService.cs" in restored
    assert restored["src/OrderService.cs"].findings[0].category == "async"

    manager.clear_checkpoint()
    assert manager.load_checkpoint() is None
