"""
Results aggregation and reporting for multi-file analysis.

Consolidates findings from multiple files and experts into structured reports.
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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
            if feedback and not feedback.startswith("No findings"):
                # Simple heuristic: extract severity from feedback
                severity = "medium"
                if any(word in feedback.lower() for word in ["critical", "deadlock", "crash"]):
                    severity = "critical"
                elif any(word in feedback.lower() for word in ["must", "should fix", "high risk"]):
                    severity = "high"
                elif any(word in feedback.lower() for word in ["consider", "could improve"]):
                    severity = "low"

                # Create a finding per paragraph/sentence cluster
                expert_summaries[expert_name] = feedback.split('\n')[0][:100]

                # For now, create one finding per expert
                # Could be enhanced to parse multiple issues
                findings.append(
                    Finding(
                        expert=expert_name,
                        issue=feedback.split('\n')[0][:200],
                        severity=severity,
                        recommendation=feedback.split('\n')[-1][:200],
                        category=expert_name.lower().replace("expert", ""),
                    )
                )

        findings = sorted(findings, key=lambda f: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(f.severity, 4))

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
        # Count findings by severity
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        category_counts: dict[str, int] = {}
        expert_activation: dict[str, int] = {}

        for file_fb in file_feedbacks:
            for finding in file_fb.findings:
                severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1
                category_counts[finding.category] = category_counts.get(finding.category, 0) + 1
                expert_activation[finding.expert] = expert_activation.get(finding.expert, 0) + 1

        summary_metrics = {
            "total_files_analyzed": len(file_feedbacks),
            "total_findings": sum(severity_counts.values()),
            "findings_by_severity": severity_counts,
            "findings_by_category": category_counts,
            "expert_activation": expert_activation,
            "files_with_critical": sum(
                1 for fb in file_feedbacks
                if any(f.severity == "critical" for f in fb.findings)
            ),
            "files_with_no_findings": sum(1 for fb in file_feedbacks if not fb.findings),
        }

        return RepositoryReport(
            repository_name="<repository>",
            analyzed_at=Path(__file__).stat().st_mtime,
            scope={},
            file_feedbacks=file_feedbacks,
            summary_metrics=summary_metrics,
        )

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
