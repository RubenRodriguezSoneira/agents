"""
Repository ingestion and file collection for multi-file C# analysis.

Supports:
- Cloning public/private GitHub repos
- Analyzing local folder structures
- Parsing .csproj files for project metadata
- Collecting C# files in dependency order
"""

import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectMetadata:
    """Metadata extracted from a .csproj file."""
    name: str
    path: Path
    target_framework: str
    dependencies: list[str]  # Names of project dependencies


class GitHubRepoFetcher:
    """Handles cloning and accessing GitHub repositories."""

    @staticmethod
    def clone_repo(
        owner: str,
        repo: str,
        branch: str = "main",
        token: Optional[str] = None,
        local_cache: Optional[Path] = None,
    ) -> Path:
        """
        Clone a GitHub repository locally.

        Args:
            owner: GitHub organization/user name
            repo: Repository name
            branch: Git branch to clone (default: main)
            token: Optional GitHub token for private repos
            local_cache: Optional cache directory; if exists, reuse it

        Returns:
            Path to cloned repository

        Raises:
            RuntimeError: If clone fails
        """
        if local_cache is None:
            local_cache = Path.home() / ".sg_repo_cache" / f"{owner}_{repo}"

        # Reuse cached clone if exists
        if local_cache.exists():
            logger.info(f"Using cached repo at {local_cache}")
            return local_cache

        local_cache.parent.mkdir(parents=True, exist_ok=True)

        # Build clone URL with token if provided
        if token:
            url = f"https://{token}@github.com/{owner}/{repo}.git"
        else:
            url = f"https://github.com/{owner}/{repo}.git"

        try:
            logger.info(f"Cloning {owner}/{repo} (branch: {branch}) to {local_cache}")
            subprocess.run(
                ["git", "clone", "--branch", branch, "--depth", "1", url, str(local_cache)],
                check=True,
                capture_output=True,
                timeout=300,
            )
            logger.info(f"Clone successful: {local_cache}")
            return local_cache
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Failed to clone {owner}/{repo}: {exc.stderr.decode()}"
            ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError("git command not found. Ensure git is installed.") from exc

    @staticmethod
    def from_local_path(local_path: str | Path) -> Path:
        """
        Use an existing local repository.

        Args:
            local_path: Path to repository root

        Returns:
            Path object pointing to repository

        Raises:
            FileNotFoundError: If path does not exist
        """
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"Local path does not exist: {local_path}")
        return path


class ProjectGraphBuilder:
    """Parses .csproj files and builds project dependency graph."""

    @staticmethod
    def parse_csproj(csproj_path: Path) -> ProjectMetadata:
        """
        Parse a .csproj file to extract metadata.

        Args:
            csproj_path: Path to .csproj file

        Returns:
            ProjectMetadata object

        Raises:
            RuntimeError: If parsing fails
        """
        try:
            tree = ET.parse(csproj_path)
            root = tree.getroot()

            # Remove namespace for easier parsing
            for elem in root.iter():
                if '}' in elem.tag:
                    elem.tag = elem.tag.split('}', 1)[1]

            # Extract project name
            project_name = csproj_path.stem

            # Extract target framework
            target_framework = "Unknown"
            for elem in root.iter("TargetFramework"):
                target_framework = elem.text or "Unknown"
                break
            if target_framework == "Unknown":
                for elem in root.iter("TargetFrameworks"):
                    frameworks = elem.text or ""
                    target_framework = frameworks.split(';')[0] if frameworks else "Unknown"
                    break

            # Extract project references (dependencies)
            dependencies = []
            for elem in root.iter("ProjectReference"):
                ref_path = elem.get("Include", "")
                if ref_path:
                    # Extract just the project name from path like "../Project.csproj"
                    ref_name = Path(ref_path).stem
                    dependencies.append(ref_name)

            return ProjectMetadata(
                name=project_name,
                path=csproj_path,
                target_framework=target_framework,
                dependencies=dependencies,
            )
        except (ET.ParseError, OSError) as exc:
            raise RuntimeError(f"Failed to parse {csproj_path}: {exc}") from exc

    @staticmethod
    def build_graph(repo_root: Path) -> dict[str, ProjectMetadata]:
        """
        Discover all .csproj files and build dependency graph.

        Args:
            repo_root: Repository root path

        Returns:
            Dictionary mapping project name to ProjectMetadata
        """
        projects: dict[str, ProjectMetadata] = {}

        for csproj_path in repo_root.rglob("*.csproj"):
            try:
                metadata = ProjectGraphBuilder.parse_csproj(csproj_path)
                projects[metadata.name] = metadata
                logger.info(
                    f"Discovered project: {metadata.name} "
                    f"(target: {metadata.target_framework}, deps: {len(metadata.dependencies)})"
                )
            except RuntimeError as exc:
                logger.warning(f"Skipping {csproj_path}: {exc}")

        return projects

    @staticmethod
    def topological_sort(projects: dict[str, ProjectMetadata]) -> list[str]:
        """
        Sort projects by dependency order (no-dep projects first).

        Args:
            projects: Dictionary of ProjectMetadata by name

        Returns:
            List of project names in topological order
        """
        visited: set[str] = set()
        visiting: set[str] = set()
        order: list[str] = []

        def visit(proj_name: str):
            if proj_name in visited:
                return
            if proj_name in visiting:
                logger.warning(f"Circular dependency detected involving {proj_name}")
                visited.add(proj_name)
                return

            visiting.add(proj_name)

            if proj_name in projects:
                for dep in projects[proj_name].dependencies:
                    if dep in projects:
                        visit(dep)

            visiting.remove(proj_name)
            visited.add(proj_name)
            order.append(proj_name)

        for proj_name in projects:
            visit(proj_name)

        return order


class CSFileCollector:
    """Collects C# files from a repository."""

    @staticmethod
    def collect(repo_root: Path, exclude_patterns: Optional[list[str]] = None) -> list[Path]:
        """
        Collect all C# files from repository, filtering by patterns.

        Args:
            repo_root: Repository root path
            exclude_patterns: Optional list of glob patterns to exclude
                             (default: bin, obj, node_modules, .git)

        Returns:
            List of .cs file paths

        Raises:
            FileNotFoundError: If repo_root does not exist
        """
        if not repo_root.exists():
            raise FileNotFoundError(f"Repository path does not exist: {repo_root}")

        if exclude_patterns is None:
            exclude_patterns = ["*/bin/*", "*/obj/*", "*/.git/*", "*/node_modules/*"]

        cs_files: list[Path] = []

        for cs_file in repo_root.rglob("*.cs"):
            # Skip generated files
            if cs_file.name.endswith(".g.cs"):
                continue

            # Skip excluded patterns
            if any(cs_file.match(pattern) for pattern in exclude_patterns):
                continue

            cs_files.append(cs_file)

        logger.info(f"Collected {len(cs_files)} C# files from {repo_root}")
        return sorted(cs_files)

    @staticmethod
    def prioritize_hot_paths(
        cs_files: list[Path],
        hot_path_keywords: Optional[list[str]] = None,
    ) -> list[Path]:
        """
        Prioritize files likely to be hot paths (controllers, services, handlers).

        Files with keywords in their name are moved to the front.

        Args:
            cs_files: List of C# file paths
            hot_path_keywords: Keywords indicating hot paths
                              (default: Controller, Service, Handler, Repository)

        Returns:
            Reordered list with hot-path files first
        """
        if hot_path_keywords is None:
            hot_path_keywords = ["Controller", "Service", "Handler", "Repository"]

        hot_path_files = []
        other_files = []

        for cs_file in cs_files:
            if any(keyword in cs_file.name for keyword in hot_path_keywords):
                hot_path_files.append(cs_file)
            else:
                other_files.append(cs_file)

        logger.info(
            f"Prioritized {len(hot_path_files)} hot-path files; "
            f"{len(other_files)} other files"
        )
        return hot_path_files + other_files


def batch_files(
    files: list[Path],
    batch_size: int = 5,
    max_tokens_per_batch: Optional[int] = None,
) -> list[list[Path]]:
    """
    Divide files into batches for parallel analysis.

    Args:
        files: List of file paths to batch
        batch_size: Target number of files per batch (default: 5)
        max_tokens_per_batch: Optional max token count per batch (for cost control)

    Returns:
        List of file batches
    """
    if max_tokens_per_batch is None:
        # Simple size-based batching
        return [files[i : i + batch_size] for i in range(0, len(files), batch_size)]

    # Token-aware batching (rough heuristic: ~4 chars per token)
    batches: list[list[Path]] = []
    current_batch: list[Path] = []
    current_tokens = 0

    for file_path in files:
        try:
            file_size = file_path.stat().st_size
            file_tokens = file_size // 4  # rough estimate
        except OSError:
            file_tokens = 1000

        if current_batch and (current_tokens + file_tokens > max_tokens_per_batch):
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append(file_path)
        current_tokens += file_tokens

    if current_batch:
        batches.append(current_batch)

    logger.info(f"Created {len(batches)} batches (batch_size={batch_size})")
    return batches
