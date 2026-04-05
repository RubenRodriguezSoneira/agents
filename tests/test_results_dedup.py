from results_aggregator import FileFeedback, Finding, ResultsAggregator


def _finding(issue: str, severity: str = "high") -> Finding:
    return Finding(
        expert="AsyncExpert",
        issue=issue,
        severity=severity,
        recommendation="Use async/await",
        category="async",
    )


def test_deduplicate_findings_with_equivalent_issue_text() -> None:
    findings = [
        _finding("Potential deadlock in .Result call"),
        _finding("potential deadlock in result call."),
        _finding("Unawaited Task may escape method", severity="medium"),
    ]

    deduped = ResultsAggregator.deduplicate_findings(findings)

    assert len(deduped) == 2
    assert deduped[0].severity == "high"
    assert deduped[1].severity == "medium"


def test_merge_feedbacks_deduplicates_across_sources() -> None:
    cache_feedback = FileFeedback(
        file_path="src/Service.cs",
        relative_path="src/Service.cs",
        findings=[_finding("Potential deadlock in .Result call")],
        expert_summaries={"AsyncExpert": "Potential deadlock in .Result call"},
    )

    resumed_feedback = FileFeedback(
        file_path="src/Service.cs",
        relative_path="src/Service.cs",
        findings=[
            _finding("potential deadlock in result call."),
            _finding("Unawaited Task may escape method", severity="medium"),
        ],
        expert_summaries={"AsyncExpert": "Unawaited Task may escape method"},
    )

    merged = ResultsAggregator.merge_feedbacks([cache_feedback, resumed_feedback])

    assert len(merged) == 1
    assert len(merged[0].findings) == 2
    assert merged[0].findings[0].severity == "high"
    assert merged[0].findings[1].severity == "medium"
