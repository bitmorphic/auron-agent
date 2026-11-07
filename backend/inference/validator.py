"""
AURON Model Output Validators
Enforces strict schema validation on Planner JSON and AST validation on Coder Python.
Source of Truth: PRD v3.0 §6 FR-04 & §7 SR-02.
"""

import ast
import json
import re
from typing import Any, Tuple


# Modules permitted inside sandbox scripts
ALLOWED_MODULES = {
    "pandas",
    "numpy",
    "matplotlib",
    "matplotlib.pyplot",
    "csv",
    "json",
    "math",
    "statistics",
    "collections",
    "itertools",
    "functools",
    "datetime",
    "pathlib",
    "os.path",
    "io",
    "re",
    "hashlib",
    "textwrap",
}

BANNED_MODULE_PREFIXES = {
    "subprocess",
    "socket",
    "requests",
    "urllib",
    "http",
    "shutil",
    "multiprocessing",
    "threading",
    "ctypes",
    "importlib",
    "pickle",
    "pty",
    "posix",
    "winreg",
}

BANNED_BUILTIN_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "breakpoint",
}

BANNED_OS_ATTRIBUTES = {
    "system",
    "popen",
    "spawn",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "fork",
    "kill",
    "remove",
    "unlink",
    "rmdir",
    "chmod",
}


def clean_json_response(raw_text: str) -> dict[str, Any]:
    """Extract and parse JSON from raw LLM completion, handling code fences."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    # Attempt direct json parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Search for first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = text[start:end + 1]
            return json.loads(snippet)
        raise ValueError("No valid JSON found in model output")


def validate_plan_schema(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate structure of Planner output dictionary."""
    required_keys = ["goal", "steps"]
    for k in required_keys:
        if k not in plan:
            raise ValueError(f"Plan missing required field: '{k}'")

    if not isinstance(plan["goal"], str) or not plan["goal"].strip():
        raise ValueError("Plan 'goal' must be a non-empty string.")

    if not isinstance(plan["steps"], list) or len(plan["steps"]) == 0:
        raise ValueError("Plan 'steps' must be a non-empty list.")

    # Defaults for optional fields
    if "columns_used" not in plan or not isinstance(plan["columns_used"], list):
        plan["columns_used"] = []
    if "assumptions" not in plan or not isinstance(plan["assumptions"], list):
        plan["assumptions"] = []
    if "expected_artifacts" not in plan or not isinstance(plan["expected_artifacts"], list):
        plan["expected_artifacts"] = ["chart.png"]
    if "data_limitations" not in plan or not isinstance(plan["data_limitations"], list):
        plan["data_limitations"] = []

    return plan


def clean_python_code(raw_text: str) -> str:
    """Extract clean Python code string from raw LLM completion."""
    text = raw_text.strip()
    # Check for complete markdown fences
    match = re.search(r"```(?:python)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # If opening fence exists without closing fence
    open_match = re.search(r"```(?:python)?\s*(.*)", text, re.DOTALL)
    if open_match:
        code_part = open_match.group(1).strip()
        if code_part.endswith("```"):
            code_part = code_part[:-3].strip()
        return code_part
    return text


def validate_python_ast(code: str) -> Tuple[bool, str]:
    """
    Validate Python code using AST parsing and safety checks.
    PRD §6 FR-04: Parse generated source with Python AST as a basic validity check.
    Returns (is_valid, error_reason).
    """
    if not code or not code.strip():
        return False, "Generated code is empty"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Python SyntaxError: {e.msg} at line {e.lineno}"
    except Exception as e:
        return False, f"AST parse error: {str(e)}"

    for node in ast.walk(tree):
        # 1. Check direct imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module in BANNED_MODULE_PREFIXES:
                    return False, f"Security violation: banned import '{alias.name}'"
                if alias.name not in ALLOWED_MODULES and root_module not in ALLOWED_MODULES:
                    # Permissive check for safe standard library submodules
                    if root_module not in {"os", "sys", "json", "csv", "math", "re", "io", "hashlib"}:
                        return False, f"Disallowed module import: '{alias.name}'"

        # 2. Check from ... import ...
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_module = node.module.split(".")[0]
                if root_module in BANNED_MODULE_PREFIXES:
                    return False, f"Security violation: banned import from '{node.module}'"
                if node.module == "os":
                    for alias in node.names:
                        if alias.name in BANNED_OS_ATTRIBUTES:
                            return False, f"Security violation: banned os attribute '{alias.name}'"

        # 3. Check forbidden function calls
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in BANNED_BUILTIN_CALLS:
                    return False, f"Security violation: forbidden built-in call '{node.func.id}()'"

            elif isinstance(node.func, ast.Attribute):
                # Check calls like os.system(...)
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    if node.func.attr in BANNED_OS_ATTRIBUTES:
                        return False, f"Security violation: forbidden call 'os.{node.func.attr}()'"

    return True, ""

