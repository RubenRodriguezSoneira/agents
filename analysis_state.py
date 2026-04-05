"""
Incremental analysis state and checkpoint management.

Tracks file fingerprints, cached feedback, commit baselines, and resumable progress.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from results_aggregator import FileFeedback, Finding

logger = logging.getLogger(__name__)

STATE_SCHEMA_VERSION = 1


def _normalize_relative_path(path: str | Path) -> str:
    return Path(path).as_posix()


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


class AnalysisStateManager:
    def __init__(self, repo_root: Path, cache_dir: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.cache_dir = cache_dir.resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.state_path = self.cache_dir / "analysis_state.json"
        self.checkpoint_path = self.cache_dir / "analysis_checkpoint.json"

    def to_relative_path(self, file_path: Path) -> str:
        try:
            return _normalize_relative_path(file_path.resolve().relative_to(self.repo_root))
        except ValueError:
            return _normalize_relative_path(file_path)

    def compute_fingerprint(self, file_path: Path) -> str:
        hasher = hashlib.sha256()
        with open(file_path, "rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def compute_fingerprints(self, files: list[Path]) -> dict[str, str]:
        fingerprints: dict[str, str] = {}
        for file_path in files:
            relative_path = self.to_relative_path(file_path)
            try:
                fingerprints[relative_path] = self.compute_fingerprint(file_path)
            except OSError as exc:
                logger.warning("Failed to fingerprint %s: %s", file_path, exc)
        return fingerprints

    def current_commit_sha(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return None

        if result.returncode != 0:
            return None

        sha = result.stdout.strip()
        return sha or None

    def changed_files_from_git(self, last_analyzed_commit: Optional[str]) -> Optional[set[str]]:
        if not last_analyzed_commit:
            return None

        try:
            inside_repo = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return None

        if inside_repo.returncode != 0:
            return None

        changed: set[str] = set()

        commands = [
            ["git", "diff", "--name-only", last_analyzed_commit, "HEAD", "--", "*.cs"],
            ["git", "diff", "--name-only", "--", "*.cs"],
            ["git", "diff", "--cached", "--name-only", "--", "*.cs"],
        ]

        for command in commands:
            result = subprocess.run(
                command,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                logger.warning("git diff command failed: %s", " ".join(command))
                return None

            for line in result.stdout.splitlines():
                if line.strip():
                    changed.add(_normalize_relative_path(line.strip()))

        return changed

    @staticmethod
    def _serialize_feedback(feedback: FileFeedback) -> dict[str, Any]:
        return {
            "file_path": feedback.file_path,
            "relative_path": feedback.relative_path,
            "findings": [asdict(finding) for finding in feedback.findings],
            "expert_summaries": feedback.expert_summaries,
        }

    @staticmethod
    def _deserialize_feedback(payload: dict[str, Any]) -> FileFeedback:
        findings = [
            Finding(
                expert=str(item.get("expert", "")),
                issue=str(item.get("issue", "")),
                severity=str(item.get("severity", "medium")),
                recommendation=str(item.get("recommendation", "")),
                category=str(item.get("category", "layering")),
            )
            for item in payload.get("findings", [])
            if isinstance(item, dict)
        ]

        return FileFeedback(
            file_path=str(payload.get("file_path", "")),
            relative_path=str(payload.get("relative_path", "")),
            findings=findings,
            expert_summaries=dict(payload.get("expert_summaries", {})),
        )

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "updated_at": _utcnow_iso(),
            "repository_root": str(self.repo_root),
            "config_signature": "",
            "last_analyzed_commit": None,
            "file_fingerprints": {},
            "cached_feedbacks": {},
        }

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._default_state()

        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load state file %s: %s", self.state_path, exc)
            return self._default_state()

        if not isinstance(payload, dict):
            return self._default_state()

        state = self._default_state()
        state.update(payload)

        cached_payload = state.get("cached_feedbacks", {})
        if not isinstance(cached_payload, dict):
            state["cached_feedbacks"] = {}

        fingerprints = state.get("file_fingerprints", {})
        if not isinstance(fingerprints, dict):
            state["file_fingerprints"] = {}

        return state

    def save_state(
        self,
        *,
        config_signature: str,
        last_analyzed_commit: Optional[str],
        file_fingerprints: dict[str, str],
        cached_feedbacks: dict[str, FileFeedback],
    ) -> None:
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "updated_at": _utcnow_iso(),
            "repository_root": str(self.repo_root),
            "config_signature": config_signature,
            "last_analyzed_commit": last_analyzed_commit,
            "file_fingerprints": {
                _normalize_relative_path(key): value for key, value in file_fingerprints.items()
            },
            "cached_feedbacks": {
                _normalize_relative_path(key): self._serialize_feedback(value)
                for key, value in cached_feedbacks.items()
            },
        }

        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def deserialize_cached_feedbacks(self, state_payload: dict[str, Any]) -> dict[str, FileFeedback]:
        cached_feedbacks: dict[str, FileFeedback] = {}
        raw_cache = state_payload.get("cached_feedbacks", {})
        if not isinstance(raw_cache, dict):
            return cached_feedbacks

        for relative_path, feedback_payload in raw_cache.items():
            if not isinstance(feedback_payload, dict):
                continue
            cached_feedbacks[_normalize_relative_path(relative_path)] = self._deserialize_feedback(
                feedback_payload
            )

        return cached_feedbacks

    def save_checkpoint(
        self,
        *,
        config_signature: str,
        completed_feedbacks: dict[str, FileFeedback],
        pending_paths: list[str],
    ) -> None:
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "updated_at": _utcnow_iso(),
            "repository_root": str(self.repo_root),
            "config_signature": config_signature,
            "completed_feedbacks": {
                _normalize_relative_path(path): self._serialize_feedback(feedback)
                for path, feedback in completed_feedbacks.items()
            },
            "pending_paths": [_normalize_relative_path(path) for path in pending_paths],
        }

        self.checkpoint_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load_checkpoint(self) -> Optional[dict[str, Any]]:
        if not self.checkpoint_path.exists():
            return None

        try:
            payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load checkpoint %s: %s", self.checkpoint_path, exc)
            return None

        if not isinstance(payload, dict):
            return None

        return payload

    def deserialize_checkpoint_feedbacks(
        self, checkpoint_payload: dict[str, Any]
    ) -> dict[str, FileFeedback]:
        completed_feedbacks: dict[str, FileFeedback] = {}
        raw_completed = checkpoint_payload.get("completed_feedbacks", {})
        if not isinstance(raw_completed, dict):
            return completed_feedbacks

        for relative_path, feedback_payload in raw_completed.items():
            if not isinstance(feedback_payload, dict):
                continue
            completed_feedbacks[_normalize_relative_path(relative_path)] = self._deserialize_feedback(
                feedback_payload
            )

        return completed_feedbacks

    def clear_checkpoint(self) -> None:
        self.checkpoint_path.unlink(missing_ok=True)


def make_config_signature(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
