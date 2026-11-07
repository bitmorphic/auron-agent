"""
End-to-End Orchestrator Integration Tests (PRD §5, §6 FR-01 to FR-10).
Executes the full pipeline:
Dataset -> Question -> Mock Planner -> Mock Coder -> AST Validation ->
Real Sandbox Execution -> Artifact & Receipt Verification -> Mock Explainer -> SQLite DB.
"""

from pathlib import Path
import pytest

from backend.config import settings
from backend.database import create_dataset, create_task, get_task, init_db
from backend.csv_tools import validate_and_parse_csv, save_uploaded_csv
from backend.inference.client import MockInferenceClient
from backend.orchestrator import Orchestrator


from tests.conftest import requires_docker


@pytest.fixture(autouse=True)
def setup_dirs(tmp_path: Path):
    settings.db_path = tmp_path / "test_orch.db"
    settings.uploads_dir = tmp_path / "uploads"
    settings.workspace_dir = tmp_path / "workspace"
    settings.receipts_dir = tmp_path / "receipts"
    settings.ensure_directories()
    init_db(settings.db_path)


@requires_docker
def test_end_to_end_orchestration_golden_path(sandbox_image: str):
    async def _test():
        settings.sandbox_image_tag = sandbox_image.split(":")[-1] if ":" in sandbox_image else sandbox_image

        # 1. Prepare and store dataset
        csv_content = (
            "region,product,revenue\n"
            "North,Alpha,1000\n"
            "South,Beta,2500\n"
            "East,Gamma,1800\n"
            "West,Delta,3200\n"
        ).encode("utf-8")

        parsed = validate_and_parse_csv(csv_content, "regions.csv")
        import uuid
        ds_id = str(uuid.uuid4())
        stored_path = save_uploaded_csv(csv_content, ds_id, settings.uploads_dir)

        ds = create_dataset(
            filename=parsed["filename"],
            storage_path=str(stored_path),
            file_hash=parsed["file_hash"],
            file_size_bytes=parsed["file_size_bytes"],
            row_count=parsed["row_count"],
            column_count=parsed["column_count"],
            encoding=parsed["encoding"],
            schema=parsed["schema"],
            preview=parsed["preview"],
            dataset_id=ds_id,
            db_path=settings.db_path,
        )

        # 2. Create task
        task = create_task(
            dataset_id=ds["id"],
            question="Which region had highest revenue? Generate a chart.",
            db_path=settings.db_path,
        )
        task_id = task["id"]

        # 3. Instantiate orchestrator with Mock client
        mock_client = MockInferenceClient()
        orchestrator = Orchestrator(inference_client=mock_client)

        # 4. Execute pipeline
        result = await orchestrator.execute_task(task_id)

        # 5. Verify results
        assert result is not None
        if result["status"] != "completed":
            print("\nTASK FAILED DETAILS:")
            print("ERROR MESSAGE:", result.get("error_message"))
            for a in result.get("attempts", []):
                print(f"ATTEMPT {a['attempt_number']}: exit={a['exit_code']}, stderr={a['stderr']}")
        assert result["status"] == "completed"
        assert result["plan"] is not None
        assert "goal" in result["plan"]
        assert result["final_code"] is not None
        assert "import pandas" in result["final_code"]
        assert result["summary_md"] is not None
        assert "Analysis" in result["summary_md"]

        # Verify attempt record
        assert len(result["attempts"]) == 1
        attempt = result["attempts"][0]
        assert attempt["exit_code"] == 0
        assert attempt["status"] == "completed"
        assert not attempt["timed_out"]
        assert not attempt["oom_killed"]
        assert attempt["stdout"] is not None

        # Verify artifacts
        assert len(result["artifacts"]) >= 1
        art_names = [a["filename"] for a in result["artifacts"]]
        assert "chart.png" in art_names

        # Verify receipt hash
        assert result["receipt_hash"] is not None
        assert len(result["receipt_hash"]) == 64  # SHA-256 hex string

        # Verify inference calls logged
        assert len(result["inference_calls"]) >= 3  # planner, coder, explainer

    import asyncio
    asyncio.run(_test())
