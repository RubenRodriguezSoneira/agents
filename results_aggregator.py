"""
Results aggregation and reporting for multi-file analysis.

Consolidates findings from multiple files and experts into structured reports.
"""

import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


REPORT_SCHEMA_VERSION = 2
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
KNOWN_CATEGORIES = ("async", "memory", "parallel", "ddd", "di", "layering")


def _short_summary(text: str, max_len: int = 100) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[0][:max_len]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _canonical_issue_text(issue: str) -> str:
    lowered = issue.lower()
    stripped = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", stripped).strip()


def _finding_signature(finding: "Finding") -> tuple[str, str, str, str]:
    return (
        finding.expert.strip().lower(),
        finding.category.strip().lower(),
        finding.severity.strip().lower(),
        _canonical_issue_text(finding.issue),
    )


def _normalize_severity(raw_value: str, context_text: str = "") -> str:
    severity = raw_value.strip().lower()
    if severity in SEVERITY_ORDER:
        return severity

    if severity in {"crit", "severe", "blocker", "urgent"}:
        return "critical"
    if severity in {"warning", "major"}:
        return "high"
    if severity in {"minor", "info", "informational"}:
        return "low"

    text = context_text.lower()
    if any(term in text for term in ["critical", "deadlock", "crash", "race condition"]):
        return "critical"
    if any(term in text for term in ["high risk", "must", "should fix", "severe"]):
        return "high"
    if any(term in text for term in ["could improve", "minor", "low"]):
        return "low"

    return "medium"


def _normalize_category(raw_value: str, expert_name: str, issue_text: str = "") -> str:
    raw = raw_value.strip().lower()
    if raw in KNOWN_CATEGORIES:
        return raw

    probe = " ".join([raw, expert_name.lower(), issue_text.lower()])

    if any(term in probe for term in ["async", "await", "deadlock"]):
        return "async"
    if any(term in probe for term in ["memory", "dispose", "gc", "heap"]):
        return "memory"
    if any(term in probe for term in ["parallel", "thread", "race", "concurrency", "lock"]):
        return "parallel"
    if any(term in probe for term in ["domain-driven", "domain model", "aggregate", "entity", "ddd"]):
        return "ddd"
    if any(term in probe for term in ["dependency injection", "service collection", "lifetime", "constructor injection"]):
        return "di"
    if any(term in probe for term in ["architecture", "layer", "boundary", "clean architecture", "onion"]):
        return "layering"

    expert_probe = expert_name.lower().replace("expert", "")
    if "async" in expert_probe:
        return "async"
    if "memory" in expert_probe:
        return "memory"
    if "parallel" in expert_probe or "concurr" in expert_probe:
        return "parallel"
    if "ddd" in expert_probe or "domain" in expert_probe:
        return "ddd"
    if expert_probe in {"di", "dependencyinjection", "dependency injection"} or "inject" in expert_probe:
        return "di"
    if "layer" in expert_probe or "architect" in expert_probe:
        return "layering"

    # Unknown experts are mapped to layering to keep a stable category set.
    return "layering"


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _extract_json_findings(feedback: str) -> Optional[list[dict[str, Any]]]:
    cleaned = _strip_code_fence(feedback)

    def _parse(payload_text: str) -> Optional[list[dict[str, Any]]]:
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, list):
            return None

        parsed = [item for item in payload if isinstance(item, dict)]
        return parsed

    parsed = _parse(cleaned)
    if parsed is not None:
        return parsed

    match = re.search(r"\[[\s\S]*\]", cleaned)
    if not match:
        return None

    return _parse(match.group(0))


def _is_no_findings_feedback(feedback: str) -> bool:
    normalized = feedback.strip().lower()
    if not normalized:
        return True

    if normalized.startswith("no findings"):
        return True

    return bool(
        re.match(r"^(async|memory|parallel|ddd|di|layering)\s*:\s*clean\.?$", normalized)
    )


def _parse_json_feedback(expert_name: str, feedback: str) -> list["Finding"]:
    payload = _extract_json_findings(feedback)
    if payload is None:
        return []

    findings: list[Finding] = []
    for item in payload:
        issue = _clean_text(item.get("issue"))
        if not issue:
            continue

        recommendation = _clean_text(item.get("recommendation"))
        if not recommendation:
            recommendation = "Follow the expert guidance to resolve this issue."

        findings.append(
            Finding(
                expert=expert_name,
                issue=issue[:200],
                severity=_normalize_severity(
                    _clean_text(item.get("severity")),
                    context_text=issue,
                ),
                recommendation=recommendation[:200],
                category=_normalize_category(
                    _clean_text(item.get("category")),
                    expert_name=expert_name,
                    issue_text=issue,
                ),
            )
        )

    return findings


def _parse_plaintext_feedback(expert_name: str, feedback: str) -> list["Finding"]:
    if _is_no_findings_feedback(feedback):
        return []

    lines = [line.strip(" -*\t") for line in feedback.splitlines() if line.strip()]
    if not lines:
        return []

    issue = lines[0]
    recommendation = lines[-1] if len(lines) > 1 else "Apply expert recommendations."

    for line in lines:
        if line.lower().startswith("issue:"):
            issue = line.split(":", 1)[1].strip() or issue
        if line.lower().startswith("recommendation:"):
            recommendation = line.split(":", 1)[1].strip() or recommendation

    if not issue:
        return []

    return [
        Finding(
            expert=expert_name,
            issue=issue[:200],
            severity=_normalize_severity("", context_text=feedback),
            recommendation=recommendation[:200],
            category=_normalize_category("", expert_name=expert_name, issue_text=feedback),
        )
    ]


@dataclass
class Finding:
    """A single finding from an expert."""
    expert: str
    issue: str
    severity: str  # "critical", "high", "medium", "low"
    recommendation: str
    category: str  # e.g., "async", "memory", "di", "ddd", etc.


@dataclass
class FileFeedback:
    """Aggregated feedback for a single file."""
    file_path: str
    relative_path: str
    findings: list[Finding]
    expert_summaries: dict[str, str]  # expert_name -> short summary


@dataclass
class RepositoryReport:
    """Complete analysis report for a repository."""
    repository_name: str
    analyzed_at: str
    scope: dict  # metadata about analysis scope
    file_feedbacks: list[FileFeedback]
    summary_metrics: dict  # aggregated statistics


class ResultsAggregator:
    """Aggregates and analyzes findings from multi-file analysis."""

    @staticmethod
    def create_file_feedback(
        file_path: Path,
        repo_root: Path,
        feedback_by_agent: dict[str, str],
    ) -> FileFeedback:
        """
        Create structured feedback for a single file.

        Args:
            file_path: Full path to analyzed file
            repo_root: Repository root (for relative path calculation)
            feedback_by_agent: Dict of agent_name -> feedback_text

        Returns:
            FileFeedback object
        """
        try:
            relative_path = file_path.relative_to(repo_root)
        except ValueError:
            relative_path = file_path

        findings: list[Finding] = []
        expert_summaries: dict[str, str] = {}

        for expert_name, feedback in feedback_by_agent.items():
            if not feedback:
                continue

            parsed_findings = _parse_json_feedback(expert_name, feedback)
            if not parsed_findings:
                parsed_findings = _parse_plaintext_feedback(expert_name, feedback)

            if parsed_findings:
                expert_summaries[expert_name] = _short_summary(feedback)
                findings.extend(parsed_findings)

        findings = sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 4))

        return FileFeedback(
            file_path=str(file_path),
            relative_path=str(relative_path),
            findings=findings,
            expert_summaries=expert_summaries,
        )

    @staticmethod
    def aggregate_findings(file_feedbacks: list[FileFeedback]) -> RepositoryReport:
        """
        Aggregate findings across all files.

        Args:
            file_feedbacks: List of FileFeedback objects

        Returns:
            Comprehensive RepositoryReport
        """
        deduplicated_feedbacks = ResultsAggregator.merge_feedbacks(file_feedbacks)

        # Count findings by severity
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        category_counts: dict[str, int] = {}
        expert_activation: dict[str, int] = {}

        for file_fb in deduplicated_feedbacks:
            for finding in file_fb.findings:
                severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1
                category_counts[finding.category] = category_counts.get(finding.category, 0) + 1
                expert_activation[finding.expert] = expert_activation.get(finding.expert, 0) + 1

        summary_metrics = {
            "total_files_analyzed": len(deduplicated_feedbacks),
            "total_findings": sum(severity_counts.values()),
            "findings_by_severity": severity_counts,
            "findings_by_category": category_counts,
            "expert_activation": expert_activation,
            "files_with_critical": sum(
                1 for fb in deduplicated_feedbacks
                if any(f.severity == "critical" for f in fb.findings)
            ),
            "files_with_no_findings": sum(1 for fb in deduplicated_feedbacks if not fb.findings),
        }

        return RepositoryReport(
            repository_name="<repository>",
            analyzed_at=datetime.now().isoformat(),
            scope={},
            file_feedbacks=deduplicated_feedbacks,
            summary_metrics=summary_metrics,
        )

    @staticmethod
    def deduplicate_findings(findings: list[Finding]) -> list[Finding]:
        unique: list[Finding] = []
        seen: set[tuple[str, str, str, str]] = set()

        for finding in findings:
            signature = _finding_signature(finding)
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(finding)

        return sorted(unique, key=lambda f: SEVERITY_ORDER.get(f.severity, 4))

    @staticmethod
    def deduplicate_file_feedback(file_feedback: FileFeedback) -> FileFeedback:
        return FileFeedback(
            file_path=file_feedback.file_path,
            relative_path=file_feedback.relative_path,
            findings=ResultsAggregator.deduplicate_findings(file_feedback.findings),
            expert_summaries=dict(file_feedback.expert_summaries),
        )

    @staticmethod
    def merge_feedbacks(file_feedbacks: list[FileFeedback]) -> list[FileFeedback]:
        merged: dict[str, FileFeedback] = {}

        for file_feedback in file_feedbacks:
            key = file_feedback.relative_path or file_feedback.file_path

            if key not in merged:
                merged[key] = ResultsAggregator.deduplicate_file_feedback(file_feedback)
                continue

            existing = merged[key]
            combined = FileFeedback(
                file_path=existing.file_path or file_feedback.file_path,
                relative_path=existing.relative_path or file_feedback.relative_path,
                findings=existing.findings + file_feedback.findings,
                expert_summaries={**existing.expert_summaries, **file_feedback.expert_summaries},
            )
            merged[key] = ResultsAggregator.deduplicate_file_feedback(combined)

        return [merged[path] for path in sorted(merged.keys())]

    @staticmethod
    def save_report(report: RepositoryReport, output_path: Path) -> None:
        """
        Save report to JSON file.

        Args:
            report: RepositoryReport to save
            output_path: Path to save JSON file
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        report_dict = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "repository_name": report.repository_name,
            "analyzed_at": str(report.analyzed_at),
            "scope": report.scope,
            "summary_metrics": report.summary_metrics,
            "file_feedbacks": [
                {
                    "file_path": fb.file_path,
                    "relative_path": fb.relative_path,
                    "findings": [asdict(f) for f in fb.findings],
                    "expert_summaries": fb.expert_summaries,
                }
                for fb in report.file_feedbacks
            ],
        }

        with open(output_path, "w") as f:
            json.dump(report_dict, f, indent=2)

        logger.info(f"Report saved to {output_path}")

    @staticmethod
    def print_summary(report: RepositoryReport) -> str:
        """
        Generate a human-readable summary of findings.

        Args:
            report: RepositoryReport to summarize

        Returns:
            Summary string
        """
        metrics = report.summary_metrics
        lines = [
            "=" * 70,
            "ANALYSIS SUMMARY",
            "=" * 70,
            f"Files analyzed: {metrics['total_files_analyzed']}",
            f"Total findings: {metrics['total_findings']}",
            "",
            "Findings by severity:",
        ]

        for severity in ["critical", "high", "medium", "low"]:
            count = metrics["findings_by_severity"].get(severity, 0)
            lines.append(f"  {severity.upper()}: {count}")

        lines.extend([
            "",
            "Findings by category:",
        ])

        for category, count in sorted(
            metrics["findings_by_category"].items(),
            key=lambda x: x[1],
            reverse=True,
        )[:10]:
            lines.append(f"  {category}: {count}")

        lines.extend([
            "",
            "Expert activation:",
        ])

        for expert, count in sorted(
            metrics["expert_activation"].items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            lines.append(f"  {expert}: {count} findings")

        lines.extend([
            "",
            f"Files with critical issues: {metrics['files_with_critical']}",
            f"Files with no findings: {metrics['files_with_no_findings']}",
            "=" * 70,
        ])

        return "\n".join(lines)
