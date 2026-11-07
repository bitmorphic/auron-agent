"""
Tests for Inference Client, Prompts, and Model Response Validators (PRD §6 FR-03, FR-04, §9).
"""

import asyncio
import json
import pytest
from backend.inference.client import MockInferenceClient
from backend.inference.validator import (
    clean_json_response,
    clean_python_code,
    validate_plan_schema,
    validate_python_ast,
)


def test_mock_inference_client_roles():
    async def _test():
        client = MockInferenceClient()

        # Planner
        p_resp = await client.chat_completion(
            system_prompt="You are AURON's AI Data Analysis Planner.",
            user_prompt="Analyze total sales",
        )
        assert p_resp["is_mock"] is True
        plan = clean_json_response(p_resp["content"])
        validated = validate_plan_schema(plan)
        assert "goal" in validated
        assert "steps" in validated

        # Coder
        c_resp = await client.chat_completion(
            system_prompt="You are AURON's AI Python Coder.",
            user_prompt="Write script for plan",
        )
        assert c_resp["is_mock"] is True
        code = clean_python_code(c_resp["content"])
        valid, reason = validate_python_ast(code)
        assert valid is True, f"Mock coder produced invalid AST: {reason}"

        # Explainer
        e_resp = await client.chat_completion(
            system_prompt="You are AURON's AI Data Analyst Explainer.",
            user_prompt="Summarize findings",
        )
        assert e_resp["is_mock"] is True
        assert "Analysis" in e_resp["content"]

    asyncio.run(_test())


def test_plan_schema_validation():
    # Valid plan
    valid_plan = {
        "goal": "Calculate mean sales",
        "steps": ["Step 1: read", "Step 2: compute"],
        "columns_used": ["sales"],
        "expected_artifacts": ["chart.png"],
    }
    validated = validate_plan_schema(valid_plan)
    assert validated["goal"] == "Calculate mean sales"

    # Missing goal
    with pytest.raises(ValueError, match="goal"):
        validate_plan_schema({"steps": ["step 1"]})

    # Missing steps
    with pytest.raises(ValueError, match="steps"):
        validate_plan_schema({"goal": "do something"})

    # Empty steps list
    with pytest.raises(ValueError, match="non-empty list"):
        validate_plan_schema({"goal": "valid", "steps": []})


def test_python_ast_validation_allowed_code():
    code = """
import json
import math
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

df = pd.read_csv('/data/input.csv')
summary = {"count": len(df), "mean": float(np.mean(df['val']))}
plt.figure()
df['val'].plot()
plt.savefig('/out/chart.png')
print(json.dumps(summary))
"""
    valid, reason = validate_python_ast(code)
    assert valid is True, reason


def test_python_ast_validation_banned_modules():
    banned_scripts = [
        "import subprocess\nsubprocess.run(['ls'])",
        "import socket\ns = socket.socket()",
        "import requests\nr = requests.get('http://evil.com')",
        "import urllib.request\n",
        "import shutil\nshutil.rmtree('/')",
        "from subprocess import Popen",
    ]
    for script in banned_scripts:
        valid, reason = validate_python_ast(script)
        assert valid is False
        assert "Security violation" in reason or "Disallowed" in reason


def test_python_ast_validation_banned_calls():
    banned_calls = [
        "eval('2 + 2')",
        "exec('import os')",
        "compile('x = 1', '', 'exec')",
        "__import__('os')",
        "import os\nos.system('rm -rf /')",
        "import os\nos.popen('whoami')",
    ]
    for script in banned_calls:
        valid, reason = validate_python_ast(script)
        assert valid is False
        assert "Security violation" in reason or "forbidden" in reason.lower()


def test_python_ast_validation_syntax_error():
    syntax_err = "def broken(\n  return 1"
    valid, reason = validate_python_ast(syntax_err)
    assert valid is False
    assert "SyntaxError" in reason
