"""
Metadata extraction and enrichment for repository-scale analysis.

Preferred path: Roslyn metadata extraction via local .NET helper project.
Fallback path: Heuristic regex extraction that works without .NET SDK or restore.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_ROSLYN_TIMEOUT_SECONDS = 300

TYPE_DECL_RE = re.compile(
    r"(?:public|internal|private|protected)?\s*"
    r"(?:abstract\s+|sealed\s+|partial\s+)*"
    r"(class|interface|record|struct)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*:\s*([^\{\n]+))?",
    re.MULTILINE,
)
NAMESPACE_RE = re.compile(r"\bnamespace\s+([A-Za-z_][A-Za-z0-9_.]*)")
ATTRIBUTE_RE = re.compile(r"\[\s*([A-Za-z_][A-Za-z0-9_]*)")
INVOCATION_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _normalize_relative_path(path: str | Path, repo_root: Optional[Path] = None) -> str:
    as_path = Path(path)
    if repo_root is not None:
        try:
            as_path = as_path.relative_to(repo_root)
        except ValueError:
            pass
    return as_path.as_posix()


def infer_architectural_layer(relative_path: str, type_name: str = "") -> str:
    probe = f"{relative_path} {type_name}".lower()

    if any(token in probe for token in ["controller", "api", "endpoint", "presentation"]):
        return "presentation"
    if any(token in probe for token in ["handler", "application", "usecase", "service"]):
        return "application"
    if any(token in probe for token in ["domain", "entity", "aggregate", "valueobject"]):
        return "domain"
    if any(token in probe for token in ["infrastructure", "repository", "persistence", "data"]):
        return "infrastructure"

    return "unknown"


def _normalize_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def _extract_type_data(source_code: str) -> tuple[str, str, str, list[str], list[str]]:
    namespace_match = NAMESPACE_RE.search(source_code)
    namespace_name = namespace_match.group(1) if namespace_match else ""

    type_match = TYPE_DECL_RE.search(source_code)
    if not type_match:
        return namespace_name, "", "", [], []

    type_kind = type_match.group(1).lower()
    type_name = type_match.group(2)
    inheritance = type_match.group(3) or ""

    base_types: list[str] = []
    interfaces: list[str] = []
    for entry in [chunk.strip() for chunk in inheritance.split(",") if chunk.strip()]:
        short_name = entry.split("<", 1)[0].strip()
        if short_name.startswith("I") and len(short_name) > 1 and short_name[1].isupper():
            interfaces.append(entry)
        elif not base_types:
            base_types.append(entry)
        else:
            interfaces.append(entry)

    return namespace_name, type_name, type_kind, base_types, interfaces


def _extract_constructor_dependencies(source_code: str, type_name: str) -> list[str]:
    if not type_name:
        return []

    constructor_re = re.compile(rf"\b{re.escape(type_name)}\s*\(([^)]*)\)")
    constructor_dependencies: list[str] = []

    for match in constructor_re.finditer(source_code):
        parameters = match.group(1).strip()
        if not parameters:
            continue

        for raw_param in parameters.split(","):
            candidate = raw_param.strip()
            if not candidate:
                continue

            candidate = re.sub(r"\[[^\]]+\]", "", candidate).strip()
            candidate = candidate.split("=", 1)[0].strip()
            tokens = [
                token
                for token in candidate.split()
                if token not in {"in", "out", "ref", "params", "this"}
            ]
            if len(tokens) < 2:
                continue

            type_token = " ".join(tokens[:-1]).strip()
            if type_token:
                constructor_dependencies.append(type_token)

    return _normalize_list(constructor_dependencies)


def _extract_attributes(source_code: str) -> list[str]:
    attributes = [match.group(1) for match in ATTRIBUTE_RE.finditer(source_code)]
    return _normalize_list(attributes)


def _extract_related_symbols(
    source_code: str,
    base_types: list[str],
    interfaces: list[str],
    constructor_dependencies: list[str],
) -> list[str]:
    invocation_names = [match.group(1) for match in INVOCATION_RE.finditer(source_code)]
    symbols = base_types + interfaces + constructor_dependencies + invocation_names
    return _normalize_list(symbols)


def _extract_heuristic_file_metadata(file_path: Path, repo_root: Path) -> Optional[dict[str, Any]]:
    try:
        source_code = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Failed to read %s for heuristic metadata: %s", file_path, exc)
        return None

    namespace_name, type_name, type_kind, base_types, interfaces = _extract_type_data(source_code)
    constructor_dependencies = _extract_constructor_dependencies(source_code, type_name)
    attributes = _extract_attributes(source_code)
    related_symbols = _extract_related_symbols(
        source_code,
        base_types=base_types,
        interfaces=interfaces,
        constructor_dependencies=constructor_dependencies,
    )

    relative_path = _normalize_relative_path(file_path, repo_root)

    return {
        "relative_path": relative_path,
        "namespace": namespace_name,
        "type_name": type_name,
        "type_kind": type_kind,
        "base_types": base_types,
        "interfaces": interfaces,
        "constructor_dependencies": constructor_dependencies,
        "attributes": attributes,
        "related_symbols": related_symbols,
        "related_files": [],
        "inferred_layer": infer_architectural_layer(relative_path, type_name=type_name),
    }


def extract_heuristic_metadata(repo_root: Path, files: list[Path]) -> dict[str, Any]:
    file_entries: dict[str, dict[str, Any]] = {}
    type_to_file: dict[str, str] = {}

    for file_path in files:
        metadata = _extract_heuristic_file_metadata(file_path, repo_root)
        if metadata is None:
            continue

        relative_path = metadata["relative_path"]
        file_entries[relative_path] = metadata

        type_name = metadata.get("type_name") or ""
        namespace_name = metadata.get("namespace") or ""
        if type_name:
            type_to_file[type_name] = relative_path
            if namespace_name:
                type_to_file[f"{namespace_name}.{type_name}"] = relative_path

    for relative_path, file_data in file_entries.items():
        related_files: list[str] = []
        for symbol_name in file_data.get("related_symbols", []):
            symbol_short = symbol_name.split(".")[-1]
            candidate = type_to_file.get(symbol_name) or type_to_file.get(symbol_short)
            if candidate and candidate != relative_path:
                related_files.append(candidate)

        file_data["related_files"] = _normalize_list(related_files)

    return {
        "metadata_extraction_status": "heuristic",
        "generated_at": datetime.utcnow().isoformat(),
        "repository_root": str(repo_root),
        "project_graph": [],
        "files": file_entries,
        "errors": [],
    }


def _run_roslyn_extractor(
    repo_root: Path,
    extractor_project: Path,
    timeout_seconds: int = DEFAULT_ROSLYN_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not extractor_project.exists():
        raise FileNotFoundError(f"Roslyn extractor project not found: {extractor_project}")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as temp_output:
        output_path = Path(temp_output.name)

    command = [
        "dotnet",
        "run",
        "--project",
        str(extractor_project),
        "--",
        "--repo-root",
        str(repo_root),
        "--output",
        str(output_path),
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("dotnet command not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Roslyn metadata extraction timed out") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        details = stderr or stdout or "No error details were returned."
        raise RuntimeError(f"Roslyn extractor failed: {details}")

    if not output_path.exists():
        raise RuntimeError("Roslyn extractor completed without writing metadata output")

    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    finally:
        output_path.unlink(missing_ok=True)

    if not isinstance(payload, dict):
        raise RuntimeError("Roslyn metadata output is not a JSON object")

    return payload


def _normalize_roslyn_payload(payload: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    files_payload = payload.get("files", {})
    normalized_files: dict[str, dict[str, Any]] = {}

    if isinstance(files_payload, dict):
        iterable = files_payload.items()
    elif isinstance(files_payload, list):
        iterable = []
        for item in files_payload:
            if isinstance(item, dict):
                key = item.get("relative_path") or item.get("relativePath")
                if key:
                    iterable.append((key, item))
    else:
        iterable = []

    for relative_path, raw_data in iterable:
        if not isinstance(raw_data, dict):
            continue

        normalized_path = _normalize_relative_path(relative_path)
        type_name = str(raw_data.get("type_name") or raw_data.get("typeName") or "")

        normalized_files[normalized_path] = {
            "relative_path": normalized_path,
            "namespace": str(raw_data.get("namespace") or ""),
            "type_name": type_name,
            "type_kind": str(raw_data.get("type_kind") or raw_data.get("typeKind") or ""),
            "base_types": _normalize_list(list(raw_data.get("base_types") or raw_data.get("baseTypes") or [])),
            "interfaces": _normalize_list(list(raw_data.get("interfaces") or [])),
            "constructor_dependencies": _normalize_list(
                list(raw_data.get("constructor_dependencies") or raw_data.get("constructorDependencies") or [])
            ),
            "attributes": _normalize_list(list(raw_data.get("attributes") or [])),
            "related_symbols": _normalize_list(list(raw_data.get("related_symbols") or raw_data.get("relatedSymbols") or [])),
            "related_files": _normalize_list(list(raw_data.get("related_files") or raw_data.get("relatedFiles") or [])),
            "inferred_layer": str(
                raw_data.get("inferred_layer")
                or raw_data.get("inferredLayer")
                or infer_architectural_layer(normalized_path, type_name=type_name)
            ),
        }

    return {
        "metadata_extraction_status": "roslyn",
        "generated_at": str(payload.get("generated_at") or payload.get("generatedAt") or datetime.utcnow().isoformat()),
        "repository_root": str(payload.get("repository_root") or payload.get("repositoryRoot") or repo_root),
        "project_graph": list(payload.get("project_graph") or payload.get("projectGraph") or []),
        "files": normalized_files,
        "errors": list(payload.get("errors") or []),
    }


def extract_repository_metadata(
    repo_root: Path,
    files: list[Path],
    extractor_project: Optional[Path] = None,
    timeout_seconds: int = DEFAULT_ROSLYN_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    extractor_project = extractor_project or (Path(__file__).resolve().parent / "RoslynMetadataExtractor")
    fallback_errors: list[str] = []

    try:
        roslyn_payload = _run_roslyn_extractor(
            repo_root=repo_root,
            extractor_project=extractor_project,
            timeout_seconds=timeout_seconds,
        )
        normalized = _normalize_roslyn_payload(roslyn_payload, repo_root=repo_root)
        logger.info("Metadata extraction status: roslyn")
        return normalized
    except Exception as exc:  # noqa: BLE001
        message = f"Roslyn metadata extraction unavailable, using heuristic fallback: {exc}"
        logger.warning(message)
        fallback_errors.append(str(exc))

    heuristic_payload = extract_heuristic_metadata(repo_root=repo_root, files=files)
    heuristic_payload["errors"] = _normalize_list(
        list(heuristic_payload.get("errors", [])) + fallback_errors
    )
    return heuristic_payload


def build_metadata_context(
    file_path: Path,
    repo_root: Path,
    metadata_bundle: Optional[dict[str, Any]],
) -> str:
    if not metadata_bundle:
        return "Metadata extraction status: none"

    status = str(metadata_bundle.get("metadata_extraction_status") or "none")
    relative_path = _normalize_relative_path(file_path, repo_root)

    file_map = metadata_bundle.get("files", {})
    file_metadata = file_map.get(relative_path) if isinstance(file_map, dict) else None

    if not isinstance(file_metadata, dict):
        return f"Metadata extraction status: {status}\nNo metadata found for this file."

    lines = [
        f"Metadata extraction status: {status}",
        f"Inferred layer: {file_metadata.get('inferred_layer', 'unknown')}",
    ]

    if file_metadata.get("namespace"):
        lines.append(f"Namespace: {file_metadata['namespace']}")
    if file_metadata.get("type_kind") or file_metadata.get("type_name"):
        lines.append(
            "Type: "
            f"{file_metadata.get('type_kind', 'unknown')} "
            f"{file_metadata.get('type_name', '').strip()}".strip()
        )

    for field_name, label in [
        ("base_types", "Base types"),
        ("interfaces", "Interfaces"),
        ("constructor_dependencies", "Constructor dependencies"),
        ("attributes", "Attributes"),
        ("related_symbols", "Related symbols"),
        ("related_files", "Related files"),
    ]:
        values = file_metadata.get(field_name, [])
        if isinstance(values, list) and values:
            lines.append(f"{label}: {', '.join(values[:15])}")

    return "\n".join(lines)
