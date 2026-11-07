"""
AURON Agent Orchestrator
Coordinates the end-to-end AI analysis lifecycle:
1. Ingest dataset & question
2. Planner agent -> Structured JSON plan
3. Coder agent -> Python source
4. AST Validation -> Import whitelist
5. Docker Sandbox execution via existing sandbox/runner.py
6. Evidence capture & Artifact verification
7. Bounded Repairer retry on eligible failures (max 2 retries)
8. Explainer agent -> Evidence-backed Markdown explanation
9. Persistent SQLite records & Receipts
Source of Truth: PRD v3.0 §5, §6 (FR-01 to FR-10), §9, §10.
"""

import asyncio
import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import settings
from backend.database import (
    get_dataset,
    get_task,
    record_artifact,
    record_attempt,
    record_inference_call,
    update_task_status,
)
from backend.inference.client import (
    BaseInferenceClient,
    InferenceError,
    get_inference_client,
)
from backend.inference.prompts import (
    CODER_SYSTEM_PROMPT,
    EXPLAINER_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    REPAIRER_SYSTEM_PROMPT,
    build_coder_user_prompt,
    build_explainer_user_prompt,
    build_planner_user_prompt,
    build_repairer_user_prompt,
)
from backend.inference.validator import (
    clean_json_response,
    clean_python_code,
    validate_plan_schema,
    validate_python_ast,
)
from sandbox.runner import SandboxRunner, SandboxPolicy

logger = logging.getLogger("auron.orchestrator")

# Concurrency semaphore bounded per PRD §6 FR-11 & §11
_EXECUTION_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _EXECUTION_SEMAPHORE
    if _EXECUTION_SEMAPHORE is None:
        _EXECUTION_SEMAPHORE = asyncio.Semaphore(settings.max_concurrent_sandboxes)
    return _EXECUTION_SEMAPHORE


class Orchestrator:
    """Manages the lifecycle of an AI data analysis task."""

    def __init__(self, inference_client: Optional[BaseInferenceClient] = None):
        self.client = inference_client or get_inference_client()
        self.runner = SandboxRunner(
            policy=SandboxPolicy(timeout_seconds=settings.sandbox_timeout_seconds),
            image_tag=settings.sandbox_image_tag,
            workspace_dir=settings.workspace_dir,
            receipts_dir=settings.receipts_dir,
        )

    async def execute_task(self, task_id: str) -> dict[str, Any]:
        """
        Execute full analysis pipeline for a registered task.
        Runs under a concurrency-limiting semaphore.
        """
        sem = _get_semaphore()
        async with sem:
            return await self._run_pipeline(task_id)

    async def _run_pipeline(self, task_id: str) -> dict[str, Any]:
        task = get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        dataset = get_dataset(task["dataset_id"])
        if not dataset:
            update_task_status(task_id, "failed", error_message="Associated dataset not found.")
            return get_task(task_id)  # type: ignore

        csv_path = Path(dataset["storage_path"])
        if not csv_path.exists():
            update_task_status(task_id, "infra_error", error_message="Dataset file missing on host.")
            return get_task(task_id)  # type: ignore

        question = task["question"]
        schema = dataset["schema"]
        preview = dataset["preview"]

        # ── Step 1: AI Planning ───────────────────────────────────────────────
        update_task_status(task_id, "planning")
        try:
            plan = await self._plan_task(task_id, question, schema, preview)
            update_task_status(task_id, "generating", plan_json=json.dumps(plan))
        except Exception as e:
            logger.error(f"Planning failed for task {task_id}: {e}")
            update_task_status(task_id, "failed", error_message=f"Planning failed: {str(e)}")
            return get_task(task_id)  # type: ignore

        # ── Step 2: Code Generation & AST Validation ──────────────────────────
        try:
            code = await self._generate_code(task_id, plan, schema, preview)
        except Exception as e:
            logger.error(f"Code generation failed for task {task_id}: {e}")
            update_task_status(task_id, "failed", error_message=f"Code generation failed: {str(e)}")
            return get_task(task_id)  # type: ignore

        # ── Step 3: Sandbox Execution & Bounded Repair Loop ───────────────────
        attempt_number = 1
        max_attempts = 1 + settings.max_retries
        latest_receipt: Optional[dict[str, Any]] = None
        current_code = code

        while attempt_number <= max_attempts:
            update_task_status(
                task_id,
                "running" if attempt_number == 1 else "retrying",
                final_code=current_code,
            )

            # Write code to a temporary script file on the host
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp_script:
                tmp_script.write(current_code)
                script_path = Path(tmp_script.name)
            script_path.chmod(0o644)

            try:
                # Run container synchronously in background thread to avoid blocking event loop
                receipt = await asyncio.to_thread(self.runner.run, csv_path, script_path)
            finally:
                try:
                    script_path.unlink(missing_ok=True)
                except Exception:
                    pass

            latest_receipt = receipt

            # Record attempt in SQLite
            exec_data = receipt["execution"]
            out_data = receipt["output"]
            dur_ms = receipt["timestamps"]["duration_ms"]

            attempt_id = record_attempt(
                task_id=task_id,
                attempt_number=attempt_number,
                code=current_code,
                exit_code=exec_data["exit_code"],
                status=exec_data["status"],
                stdout=out_data["stdout"],
                stderr=out_data["stderr"],
                stdout_truncated=out_data["stdout_truncated"],
                stderr_truncated=out_data["stderr_truncated"],
                duration_ms=dur_ms,
                timed_out=exec_data["timed_out"],
                oom_killed=exec_data["oom_killed"],
                receipt_json=json.dumps(receipt),
            )

            # Record validated artifacts
            for art in receipt.get("artifacts", []):
                art_name = art["name"]
                art_path = str(settings.receipts_dir / f"{receipt['task_id']}_{receipt['attempt_id']}" / "artifacts" / art_name)
                record_artifact(
                    task_id=task_id,
                    filename=art_name,
                    storage_path=art_path,
                    file_size=art.get("size", art.get("size_bytes", 0)),
                    sha256=art["sha256"],
                    mime=art.get("mime_type", art.get("mime", "application/octet-stream")),
                    attempt_id=attempt_id,
                )

            # Check for success
            if exec_data["exit_code"] == 0 and not exec_data["timed_out"] and not exec_data["oom_killed"]:
                logger.info(f"Task {task_id} execution succeeded on attempt {attempt_number}")
                break

            # Check if repair is eligible (PRD §6 FR-07: no retry on timeout, OOM, or max retries exceeded)
            if exec_data["timed_out"] or exec_data["oom_killed"]:
                logger.warning(f"Task {task_id} failed with timeout/OOM; repair not permitted.")
                break

            if attempt_number >= max_attempts:
                logger.warning(f"Task {task_id} exhausted max retries ({max_attempts}).")
                break

            # Attempt repair
            logger.info(f"Attempting code repair for task {task_id} (attempt {attempt_number + 1})...")
            try:
                current_code = await self._repair_code(
                    task_id=task_id,
                    prior_code=current_code,
                    stderr=out_data["stderr"],
                    plan=plan,
                    schema=schema,
                )
                attempt_number += 1
            except Exception as e:
                logger.error(f"Repair agent failed for task {task_id}: {e}")
                break

        # ── Step 4: Explanation & Verification ────────────────────────────────
        if latest_receipt is None:
            update_task_status(task_id, "failed", error_message="Execution produced no receipt.")
            return get_task(task_id)  # type: ignore

        exec_data = latest_receipt["execution"]
        out_data = latest_receipt["output"]
        artifacts = latest_receipt.get("artifacts", [])

        now_iso = datetime.now(timezone.utc).isoformat()
        is_success = (exec_data["exit_code"] == 0 and not exec_data["timed_out"] and not exec_data["oom_killed"])

        if is_success:
            update_task_status(task_id, "explaining")
            try:
                explanation = await self._explain_results(
                    task_id=task_id,
                    question=question,
                    plan=plan,
                    stdout=out_data["stdout"],
                    artifacts=artifacts,
                    exit_code=exec_data["exit_code"],
                )
            except Exception as e:
                logger.warning(f"Explainer agent failed: {e}. Falling back to default summary.")
                explanation = "Analysis executed successfully. Captured stdout and artifacts are available."

            update_task_status(
                task_id,
                "completed",
                summary_md=explanation,
                receipt_hash=latest_receipt["receipt_hash"],
                completed_at=now_iso,
            )
        else:
            err = out_data["stderr"] or f"Execution failed with status: {exec_data['status']}"
            update_task_status(
                task_id,
                "failed",
                error_message=err[:1000],
                receipt_hash=latest_receipt["receipt_hash"],
                completed_at=now_iso,
            )

        return get_task(task_id)  # type: ignore

    # ── Role Methods ─────────────────────────────────────────────────────────

    async def _plan_task(
        self,
        task_id: str,
        question: str,
        schema: dict[str, Any],
        preview: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Invoke Planner agent and validate returned plan schema."""
        user_prompt = build_planner_user_prompt(question, schema, preview)
        resp = await self.client.chat_completion(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,
        )

        record_inference_call(
            task_id=task_id,
            role="planner",
            model_name=resp.get("returned_model", resp["model"]),
            requested_model=resp.get("requested_model", resp["model"]),
            returned_model=resp.get("returned_model", resp["model"]),
            prompt_tokens=resp["prompt_tokens"],
            completion_tokens=resp["completion_tokens"],
            duration_ms=resp["duration_ms"],
            success=True,
        )

        try:
            raw_plan = clean_json_response(resp["content"])
            return validate_plan_schema(raw_plan)
        except Exception as e:
            # PRD §6 FR-03: allow one constrained re-ask
            logger.warning(f"Plan validation failed ({e}), requesting JSON correction...")
            retry_prompt = f"{user_prompt}\n\nIMPORTANT: Your previous output had format errors. Return STRICT VALID JSON ONLY following the required schema."
            retry_resp = await self.client.chat_completion(
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_prompt=retry_prompt,
                temperature=0.0,
            )
            raw_plan = clean_json_response(retry_resp["content"])
            return validate_plan_schema(raw_plan)

    async def _generate_code(
        self,
        task_id: str,
        plan: dict[str, Any],
        schema: dict[str, Any],
        preview: list[dict[str, Any]],
    ) -> str:
        """Invoke Coder agent and validate Python AST."""
        user_prompt = build_coder_user_prompt(plan, schema, preview)
        resp = await self.client.chat_completion(
            system_prompt=CODER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=4096,
        )

        record_inference_call(
            task_id=task_id,
            role="coder",
            model_name=resp.get("returned_model", resp["model"]),
            requested_model=resp.get("requested_model", resp["model"]),
            returned_model=resp.get("returned_model", resp["model"]),
            prompt_tokens=resp["prompt_tokens"],
            completion_tokens=resp["completion_tokens"],
            duration_ms=resp["duration_ms"],
            success=True,
        )

        code = clean_python_code(resp["content"])
        is_valid, reason = validate_python_ast(code)
        if not is_valid:
            raise ValueError(f"Generated Python code failed AST validation: {reason}")
        return code

    async def _repair_code(
        self,
        task_id: str,
        prior_code: str,
        stderr: str,
        plan: dict[str, Any],
        schema: dict[str, Any],
    ) -> str:
        """Invoke Repairer agent to fix failed execution script."""
        user_prompt = build_repairer_user_prompt(prior_code, stderr, plan, schema)
        resp = await self.client.chat_completion(
            system_prompt=REPAIRER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=4096,
        )

        record_inference_call(
            task_id=task_id,
            role="repairer",
            model_name=resp.get("returned_model", resp["model"]),
            requested_model=resp.get("requested_model", resp["model"]),
            returned_model=resp.get("returned_model", resp["model"]),
            prompt_tokens=resp["prompt_tokens"],
            completion_tokens=resp["completion_tokens"],
            duration_ms=resp["duration_ms"],
            success=True,
        )

        code = clean_python_code(resp["content"])
        is_valid, reason = validate_python_ast(code)
        if not is_valid:
            raise ValueError(f"Repaired code failed AST validation: {reason}")
        return code

    async def _explain_results(
        self,
        task_id: str,
        question: str,
        plan: dict[str, Any],
        stdout: str,
        artifacts: list[dict[str, Any]],
        exit_code: int,
    ) -> str:
        """Invoke Explainer agent to summarize findings from captured evidence."""
        user_prompt = build_explainer_user_prompt(
            question=question,
            plan=plan,
            stdout=stdout,
            artifacts=artifacts,
            exit_code=exit_code,
        )
        resp = await self.client.chat_completion(
            system_prompt=EXPLAINER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.3,
        )

        record_inference_call(
            task_id=task_id,
            role="explainer",
            model_name=resp.get("returned_model", resp["model"]),
            requested_model=resp.get("requested_model", resp["model"]),
            returned_model=resp.get("returned_model", resp["model"]),
            prompt_tokens=resp["prompt_tokens"],
            completion_tokens=resp["completion_tokens"],
            duration_ms=resp["duration_ms"],
            success=True,
        )

        return resp["content"]
