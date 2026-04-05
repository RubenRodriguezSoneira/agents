from __future__ import annotations

from pathlib import Path

from metadata_enricher import build_metadata_context, extract_repository_metadata


SAMPLE_CODE = """
namespace Demo.Api;

[ApiController]
public class OrdersController : ControllerBase, IDisposable
{
    private readonly IOrderService _service;

    public OrdersController(IOrderService service, ILogger<OrdersController> logger)
    {
        _service = service;
    }

    public async Task<IActionResult> GetOrder(int id)
    {
        await _service.GetAsync(id);
        return Ok();
    }
}
""".strip()


def test_extract_repository_metadata_heuristic_fallback(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    file_path = repo_root / "src" / "Controllers" / "OrdersController.cs"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(SAMPLE_CODE, encoding="utf-8")

    payload = extract_repository_metadata(
        repo_root=repo_root,
        files=[file_path],
        extractor_project=tmp_path / "missing_roslyn_project",
    )

    assert payload["metadata_extraction_status"] == "heuristic"
    assert payload["files"]["src/Controllers/OrdersController.cs"]["namespace"] == "Demo.Api"
    assert payload["files"]["src/Controllers/OrdersController.cs"]["type_name"] == "OrdersController"
    assert payload["files"]["src/Controllers/OrdersController.cs"]["inferred_layer"] == "presentation"


def test_build_metadata_context_contains_key_sections(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    file_path = repo_root / "src" / "Controllers" / "OrdersController.cs"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(SAMPLE_CODE, encoding="utf-8")

    payload = extract_repository_metadata(
        repo_root=repo_root,
        files=[file_path],
        extractor_project=tmp_path / "missing_roslyn_project",
    )

    context = build_metadata_context(file_path=file_path, repo_root=repo_root, metadata_bundle=payload)

    assert "Metadata extraction status: heuristic" in context
    assert "Namespace: Demo.Api" in context
    assert "Type: class OrdersController" in context
    assert "Inferred layer: presentation" in context
