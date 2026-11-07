"""Pytest configuration and shared fixtures for AURON tests."""

import platform
import shutil
import subprocess

import pytest

# ─── Environment detection ───────────────────────────────────────────────────

try:
    from sandbox.runner import DOCKER_CMD
    DOCKER_AVAILABLE = True
except (FileNotFoundError, ImportError):
    DOCKER_CMD = "docker"
    DOCKER_AVAILABLE = shutil.which("docker") is not None


def _ensure_docker_desktop_path() -> None:
    """Add Docker Desktop directories to PATH if missing on Windows."""
    if platform.system() != "Windows":
        return
    import os
    docker_bin = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Programs", "DockerDesktop", "resources", "bin",
    )
    docker_cred = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        "Docker", "Docker", "resources", "bin",
    )
    for d in (docker_bin, docker_cred):
        if os.path.isdir(d) and d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


_ensure_docker_desktop_path()


def _check_docker_running() -> bool:
    """Return True if Docker daemon is reachable."""
    if not DOCKER_AVAILABLE:
        return False
    try:
        result = subprocess.run(
            [DOCKER_CMD, "info"],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


DOCKER_RUNNING = _check_docker_running()
ON_WINDOWS = platform.system() == "Windows"

# ─── Markers ─────────────────────────────────────────────────────────────────

requires_docker = pytest.mark.skipif(
    not DOCKER_RUNNING,
    reason="Docker not available or not running",
)

linux_only = pytest.mark.skipif(
    ON_WINDOWS,
    reason="Requires Linux Docker host — final validation on Vultr Ubuntu VM",
)


# ─── Session-scoped fixtures ─────────────────────────────────────────────────


@pytest.fixture(scope="session")
def sandbox_image():
    """Build the sandbox image once per test session (or reuse if it exists)."""
    if not DOCKER_RUNNING:
        pytest.skip("Docker not available")

    image_name = "auron-sandbox:test"

    # Check if image already exists to avoid rebuild failures
    # when docker-credential-desktop is not on PATH
    check = subprocess.run(
        [DOCKER_CMD, "image", "inspect", image_name],
        capture_output=True, timeout=15,
    )
    if check.returncode == 0:
        yield image_name
        return

    # Image doesn't exist — build it
    from sandbox.runner import build_image

    image_name = build_image(tag="test")
    yield image_name


# ─── Per-test fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def sample_csv(tmp_path):
    """Create a small sample CSV for testing."""
    csv_content = (
        "date,product,quantity,price,region\n"
        "2024-01-15,Widget A,100,29.99,North\n"
        "2024-01-16,Widget B,50,49.99,South\n"
        "2024-01-17,Widget C,75,19.99,East\n"
        "2024-01-18,Widget A,120,29.99,West\n"
        "2024-01-19,Widget B,80,49.99,North\n"
    )
    csv_file = tmp_path / "test_data.csv"
    csv_file.write_text(csv_content, encoding="utf-8")
    return csv_file


@pytest.fixture
def simple_script(tmp_path):
    """A script that reads CSV and prints JSON result."""
    script = tmp_path / "analysis.py"
    script.write_text(
        "import pandas as pd, json\n"
        "df = pd.read_csv('/data/input.csv')\n"
        "result = {'rows': len(df), 'columns': list(df.columns),\n"
        "          'total_quantity': int(df['quantity'].sum())}\n"
        "print(json.dumps(result))\n",
        encoding="utf-8",
    )
    return script


@pytest.fixture
def failing_script(tmp_path):
    """A script that exits with non-zero status."""
    script = tmp_path / "fail.py"
    script.write_text(
        "import sys\n"
        "print('About to fail', file=sys.stderr)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    return script


@pytest.fixture
def timeout_script(tmp_path):
    """A script that runs until killed."""
    script = tmp_path / "timeout.py"
    script.write_text(
        "import time\n"
        "while True:\n"
        "    time.sleep(0.5)\n",
        encoding="utf-8",
    )
    return script


@pytest.fixture
def artifact_script(tmp_path):
    """A script that produces output artifacts (PNG, CSV, JSON)."""
    script = tmp_path / "artifacts.py"
    script.write_text(
        "import pandas as pd\n"
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "import json\n"
        "\n"
        "df = pd.read_csv('/data/input.csv')\n"
        "\n"
        "# Chart\n"
        "fig, ax = plt.subplots()\n"
        "df.groupby('product')['quantity'].sum().plot(kind='bar', ax=ax)\n"
        "plt.title('Sales by Product')\n"
        "plt.tight_layout()\n"
        "plt.savefig('/out/chart.png')\n"
        "plt.close()\n"
        "\n"
        "# Summary CSV\n"
        "summary = df.groupby('product')['quantity'].sum().reset_index()\n"
        "summary.to_csv('/out/summary.csv', index=False)\n"
        "\n"
        "# Result JSON\n"
        "result = {'products': len(df['product'].unique()),\n"
        "          'total': int(df['quantity'].sum())}\n"
        "with open('/out/result.json', 'w') as f:\n"
        "    json.dump(result, f)\n"
        "\n"
        "print(json.dumps(result))\n",
        encoding="utf-8",
    )
    return script

