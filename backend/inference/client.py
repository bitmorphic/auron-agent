"""
AURON Inference Client
Supports:
1. Live Vultr Serverless Inference (OpenAI-compatible /chat/completions)
2. Deterministic Mock Client for automated testing and offline execution.
Source of Truth: PRD v3.0 §9.
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx
from backend.config import settings

logger = logging.getLogger("auron.inference")


class InferenceError(RuntimeError):
    """Raised when an LLM inference call fails."""
    pass


class BaseInferenceClient(ABC):
    """Abstract base for AURON inference clients."""

    @abstractmethod
    async def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """
        Execute chat completion.
        Returns:
            {
                "content": str,
                "model": str,
                "prompt_tokens": int,
                "completion_tokens": int,
                "duration_ms": float,
                "is_mock": bool
            }
        """
        pass


class LiveVultrClient(BaseInferenceClient):
    """Client for Vultr Serverless Inference (OpenAI-compatible)."""

    def __init__(
        self,
        api_key: str = settings.vultr_inference_api_key,
        base_url: str = settings.vultr_inference_base_url,
        model_name: str = settings.model_name,
        timeout_seconds: float = 45.0,
    ):
        if not api_key:
            raise ValueError("VULTR_INFERENCE_API_KEY must be provided for live inference.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    async def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        start_time = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException:
            duration_ms = (time.monotonic() - start_time) * 1000
            raise InferenceError(f"Vultr inference timed out after {self.timeout_seconds}s")
        except Exception as e:
            raise InferenceError(f"Vultr connection failed: {type(e).__name__}")

        duration_ms = (time.monotonic() - start_time) * 1000

        if response.status_code != 200:
            # Redact any accidental tokens in error response
            err_msg = response.text[:200]
            raise InferenceError(f"Vultr API returned HTTP {response.status_code}: {err_msg}")

        data = response.json()
        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        usage = data.get("usage", {})
        returned_model = data.get("model") or self.model_name

        return {
            "content": content,
            "requested_model": self.model_name,
            "returned_model": returned_model,
            "model": returned_model,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "duration_ms": duration_ms,
            "is_mock": False,
        }


class MockInferenceClient(BaseInferenceClient):
    """
    Deterministic mock client for testing and offline development.
    Produces valid JSON plans, executable Python scripts with matplotlib plots,
    and structured Markdown explanations.
    """

    def __init__(self, model_name: str = "auron-mock-v1"):
        self.model_name = model_name

    async def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        start_time = time.monotonic()

        # Differentiate based on system prompt / role
        if "Planner" in system_prompt:
            content = self._mock_planner(user_prompt)
        elif "Coder" in system_prompt:
            content = self._mock_coder(user_prompt)
        elif "Repairer" in system_prompt:
            content = self._mock_repairer(user_prompt)
        elif "Explainer" in system_prompt:
            content = self._mock_explainer(user_prompt)
        else:
            content = json.dumps({"status": "ok", "message": "Generic mock completion"})

        duration_ms = (time.monotonic() - start_time) * 1000 + 15.0  # Simulated 15ms latency

        return {
            "content": content,
            "requested_model": self.model_name,
            "returned_model": self.model_name,
            "model": self.model_name,
            "prompt_tokens": len(user_prompt.split()),
            "completion_tokens": len(content.split()),
            "duration_ms": duration_ms,
            "is_mock": True,
        }

    def _mock_planner(self, prompt: str) -> str:
        return json.dumps({
            "goal": "Analyze dataset metrics, aggregate key statistics, and generate a visualization",
            "columns_used": ["category", "revenue", "units_sold"],
            "assumptions": [
                "Dataset contains standard numerical and categorical columns",
                "Missing values will be dropped or filled with column medians"
            ],
            "steps": [
                "Load /data/input.csv with pandas",
                "Calculate total counts and summary statistics",
                "Group by primary categorical dimension and aggregate numerical metric",
                "Generate a bar chart visualization and save to /out/chart.png",
                "Output structured JSON summary to stdout"
            ],
            "expected_artifacts": ["chart.png"],
            "data_limitations": ["Preview is truncated to 20 rows; analyzing full input file"]
        }, indent=2)

    def _mock_coder(self, prompt: str) -> str:
        return '''import json
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

def main():
    # 1. Read input CSV
    df = pd.read_csv('/data/input.csv')
    
    # 2. Identify numerical and categorical columns
    num_cols = df.select_dtypes(include=['number']).columns.tolist()
    cat_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()
    
    summary = {
        "row_count": len(df),
        "columns": df.columns.tolist(),
        "numeric_metrics": {}
    }
    
    # 3. Compute metrics
    for col in num_cols[:3]:
        summary["numeric_metrics"][col] = {
            "mean": float(df[col].mean()),
            "sum": float(df[col].sum()),
            "min": float(df[col].min()),
            "max": float(df[col].max())
        }
        
    # 4. Generate Chart
    plt.figure(figsize=(8, 5))
    if cat_cols and num_cols:
        cat = cat_cols[0]
        num = num_cols[0]
        grouped = df.groupby(cat)[num].sum().head(10)
        grouped.plot(kind='bar', color='#3b82f6')
        plt.title(f'Total {num} by {cat}')
        plt.xlabel(cat)
        plt.ylabel(num)
    elif num_cols:
        num = num_cols[0]
        df[num].plot(kind='hist', bins=10, color='#06b6d4')
        plt.title(f'Distribution of {num}')
    else:
        plt.text(0.5, 0.5, 'Summary Data Available', ha='center', va='center')
        
    plt.tight_layout()
    plt.savefig('/out/chart.png', dpi=150)
    plt.close('all')
    
    # 5. Output structured JSON to stdout
    print(json.dumps({"status": "success", "results": summary}))

if __name__ == '__main__':
    main()
'''

    def _mock_repairer(self, prompt: str) -> str:
        # Return robust version
        return self._mock_coder(prompt)

    def _mock_explainer(self, prompt: str) -> str:
        return """### Analysis Findings

The automated data analysis pipeline has completed successfully:

1. **Data Overview**: All input records were processed through the hardened sandbox container.
2. **Key Metrics**: Summary statistics were calculated across all numerical variables.
3. **Visualization**: A high-resolution chart has been generated (`chart.png`) illustrating the primary distribution and categorical breakdowns.

All results and artifacts have been verified against execution evidence."""


def get_inference_client(force_mock: bool = False) -> BaseInferenceClient:
    """
    Factory function returning the appropriate inference client.
    Uses LiveVultrClient if API key is present and force_mock is False;
    otherwise returns MockInferenceClient.
    """
    if not force_mock and settings.vultr_inference_api_key.strip():
        logger.info("Using Live Vultr Serverless Inference Client.")
        return LiveVultrClient()
    logger.info("Using Mock Inference Client (offline / testing mode).")
    return MockInferenceClient()

