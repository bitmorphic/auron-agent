<div align="center">
  <img src="assets/banner.png" alt="Vultr Agent Rush Hackathon" width="80%">
  <br><br>
  
  <b>Team:</b> Brute Force<br>
  <b>Participant:</b> Mohammad Anas (Solo Member)
  <br><br>

# AURON — Autonomous Analysis. Verified Execution.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-hardened%20sandbox-blue.svg)](https://www.docker.com/)
[![Vultr Serverless Inference](https://img.shields.io/badge/vultr-serverless%20inference-blue.svg)](https://www.vultr.com/products/serverless-inference/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.115+-green.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/tests-145%2F145%20passing-brightgreen.svg)](https://github.com/)

</div>
An AI data-analysis agent that executes generated Python code inside disposable, isolated Docker containers on a dedicated Vultr VM, backed by tamper-evident SHA-256 receipts and powered by Vultr Serverless Inference.

> **Hackathon Track:** Vultr Agent Rush — Challenge 1: Blast Radius Zero  
> **Source of Truth:** [`docs/PRD.md`](docs/PRD.md) (v3.0) & [`AGENTS.md`](AGENTS.md)  
> **Product Promise:** Ask a question. Get real analysis. Inspect what ran and the boundaries around it.

---

## Live Production Deployment

AURON is deployed on dedicated Vultr cloud infrastructure:
- **Public Application URL:** [`https://45.77.19.39`](https://45.77.19.39) (and `http://45.77.19.39`)
- **Infrastructure:** Vultr Dedicated Cloud Compute VM (VX1 Dedicated: 4 vCPU, 32 GB RAM, 240 GB NVMe, Ubuntu 24.04.5 LTS, Tokyo, Japan)
- **Model In Use:** `deepseek-v4.1-flash-1017` on Vultr Serverless Inference (`https://api.vultrinference.com/v1`)
- **Reverse Proxy:** Caddy v2.11 with automatic TLS, security headers (`nosniff`, `SAMEORIGIN`), and access logging
- **Service Management:** systemd service (`auron.service`) running under a dedicated non-root application user (`auron`)
- **Firewall:** UFW active with minimal open ports (`22`, `80`, `443`)

---

## Core Value Proposition: Blast Radius Zero

Most AI code-execution agents execute untrusted LLM-generated code in persistent virtualenvs or permissive containers. AURON enforces strict, kernel-level **Blast Radius Zero**:

1. **Total Network Namespace Isolation:** Containers run with `--network none`. No data can be exfiltrated; no external APIs or command-and-control servers can be reached.
2. **Immutable Read-Only Root Filesystem:** Root filesystem is mounted read-only (`--read-only`). System binaries, libraries, and Python packages cannot be altered.
3. **Non-Root Execution:** Code executes strictly as non-root UID `10001:10001`. Container breakout exploits relying on root privileges are neutralized.
4. **All Linux Capabilities Dropped:** `--cap-drop ALL` strips all root privileges (raw sockets, ptrace, module loading).
5. **Privilege Escalation Prevention:** `--security-opt no-new-privileges` prevents `setuid` binaries from escalating privileges.
6. **Kernel Resource Limits:** Strict runtime cgroups limits: 512 MB RAM, 64 PIDs, 1.0 CPU quota, 60s hard timeout.
7. **Bounded Execution Storage:** `/out` is mounted as a 20 MB bounded tmpfs (`rw,noexec,nosuid`). Storage limit is kernel-enforced during execution. Artifacts are safely extracted via controller tar streams before container destruction.
8. **Evidence-Backed Containment Card & Tamper-Evident Receipts:** Every execution receipt inspects real Docker daemon metadata and computes canonical SHA-256 digests over inputs, generated code, execution outcomes, and output artifacts to detect post-run tampering.

---

## Red Team Arena: Active Boundary Verification

AURON includes a built-in **Red Team Arena** running bounded security probes against disposable test containers. All outcomes are classified strictly from controller-side evidence:

| Probe ID | Target Control | Mechanism | Expected Outcome | Live Observed Classification |
|---|---|---|---|---|
| `network_isolation` | `--network none` | Docker Network Namespace Isolation | Outbound TCP/UDP sockets and DNS fail | **BLOCKED** (`OSError: [Errno 101] Network is unreachable`) |
| `secret_isolation` | Clean Env Allowlist & UID 10001 | Filtered env allowlist, zero secret mounts | Zero API keys, SSH keys, or system files accessible | **BLOCKED** (No sensitive vars or `/root` accessible) |
| `pid_limit` | `--pids-limit 64` | Linux cgroups PID Controller (`pids.max`) | Fork bomb halted at 64 PIDs | **BLOCKED** (`BlockingIOError: Resource temporarily unavailable` at 62 PIDs) |

> **Honest Classification Policy:** If a probe were ever to bypass a control, AURON reports it as a **`SUCCEEDED` security finding**, never disguising it as a block.

---

## System Architecture

```text
User Browser / Dashboard
       │
       ▼  (HTTPS)
[Caddy Reverse Proxy]
       │  (127.0.0.1:8000)
[FastAPI Backend Orchestrator]
├── SQLite Persistence (WAL Mode, UUID Keys, RedTeam Audit)
├── Untrusted CSV Ingestion & Quality Profiling
├── 4-Role AI Orchestrator (Planner → Coder → Repairer → Explainer)
│     │
│     ▼ (Vultr Serverless Inference API: deepseek-v4.1-flash-1017)
│   LLM Generates Verified Python AST
│
└── Trusted Sandbox Controller (SandboxRunner)
      │  (Unix Domain Socket /var/run/docker.sock)
      ▼
[Disposable Docker Container: auron-sandbox:latest]
├── UID 10001:10001 | --network none | --read-only
├── --cap-drop ALL | --security-opt no-new-privileges
├── --pids-limit 64 | --memory 512m | --cpus 1.0
├── Read-only bind mount: /data/input.csv
├── Read-only bind mount: /app/code.py
└── Bounded tmpfs: /tmp (64MB) and /out (20MB)
      │
      ▼  (Host tar-stream extraction & tamper-evident verification)
Captured Evidence & Containment Card
└── SHA-256 Receipts, Charts, Summary Explanations
```

### Four Distinct AI Roles
1. **Planner (`PlannerAgent`):** Analyzes the validated CSV schema and user question to construct a strictly typed step-by-step analysis plan in JSON.
2. **Coder (`CoderAgent`):** Translates the plan into self-contained Python code adhering to strict sandbox safety conventions. Verified by Python AST parsing.
3. **Repairer (`RepairerAgent`):** Invoked automatically on runtime errors or syntax faults. Receives traceback and failure evidence to generate a single targeted repair attempt.
4. **Explainer (`ExplainerAgent`):** Reviews captured stdout, dataset context, and execution status to generate an executive natural-language summary and data insights.

---

## Vultr Services Utilized

| Service | Role in AURON | Configuration |
|---|---|---|
| **Vultr Dedicated Cloud Compute VM** | Production host running backend, Docker daemon, and Caddy | VX1 Dedicated (4 vCPU, 32 GB RAM, 240 GB NVMe), Ubuntu 24.04.5 LTS, Tokyo |
| **Vultr Serverless Inference** | OpenAI-compatible serverless LLM inference for all 4 agent roles | `https://api.vultrinference.com/v1`, Model: `deepseek-v4.1-flash-1017` |

---

## API Reference

| Endpoint | Method | Auth / Headers | Description |
|---|---|---|---|
| `/api/health` | `GET` | None | Service status, Docker availability, inference mode, and configured model name |
| `/api/datasets` | `GET` | None | List all available datasets (including pre-seeded sample data) |
| `/api/datasets/upload` | `POST` | None | Upload a new CSV file (multipart/form-data, capped at 10 MB, 100k rows, 200 cols) |
| `/api/datasets/sample` | `POST` | None | Load a bundled sample dataset (`sales_clean`, `customer_churn`) |
| `/api/datasets/{id}` | `GET` | None | Retrieve dataset schema, column profiling, preview rows, and SHA-256 hash |
| `/api/tasks` | `POST` | None | Submit an analysis question for a dataset (triggers asynchronous AI orchestration) |
| `/api/tasks/{id}` | `GET` | None | Retrieve full task status, generated plan/code, attempts, artifacts, and Containment Card |
| `/api/tasks/{id}/receipt` | `GET` | None | Download the canonical, tamper-evident JSON execution receipt |
| `/api/tasks/{id}/artifacts/{name}` | `GET` | None | Securely download generated charts, tables, or artifacts (path traversal defended) |
| `/api/redteam/probes` | `GET` | None | List registered Red Team security probes and descriptions |
| `/api/redteam/{probe_id}/run` | `POST` | `X-RedTeam-Token` | Execute a bounded security probe against a fresh disposable container |
| `/api/redteam/runs` | `GET` | `X-RedTeam-Token` | Retrieve persistent audit history of past Red Team probe runs |

---

## Try AURON with the Demo Dataset

The exact dataset used in our recorded product demonstration is included in this repository.

**Download:** [`examples/auron-demo-sales-5000.csv`](examples/auron-demo-sales-5000.csv)

### Dataset Schema

| Column | Type | Description |
|---|---|---|
| `date` | `YYYY-MM-DD` | Transaction date (2025-01-01 to 2025-12-31) |
| `product` | string | Product name (8 distinct products) |
| `category` | string | Product category (`Electronics`, `Accessories`) |
| `region` | string | Sales region (`North`, `South`, `East`, `West`) |
| `units_sold` | integer | Units sold in the transaction |
| `unit_price` | decimal | Unit price in USD |
| `revenue` | decimal | Total transaction revenue (`units_sold × unit_price`) |

**Rows:** 5,000 &nbsp;|&nbsp; **Columns:** 7 &nbsp;|&nbsp; **SHA-256:** `071032a26469f8f6543bf8cb22ab3decbba28904aa7ceb95e96fdb96b15b2388`

### Reproducible Analysis Prompt

Upload `auron-demo-sales-5000.csv` to AURON's Analysis Workspace and enter:

> *Which product generated the highest total revenue? Calculate total revenue for every product, rank them from highest to lowest, and generate a bar chart.*

### Expected Results (Verified Against Actual CSV)

| Metric | Value |
|---|---|
| Total revenue | **\$44,017,002.80** |
| Average revenue per row | **\$8,803.40** |
| Top product | **Alpha Laptop** |
| Top-product revenue | **\$21,220,416.00** |
| Revenue calculation mismatches | **0** |

**All product revenues (descending):**

| Product | Revenue |
|---|---|
| Alpha Laptop | \$21,220,416.00 |
| Theta Camera | \$8,269,341.52 |
| Zeta Tablet | \$6,134,503.50 |
| Gamma Monitor | \$4,536,451.50 |
| Eta Dock | \$1,452,399.85 |
| Delta Keyboard | \$1,122,166.20 |
| Epsilon Headset | \$836,223.90 |
| Beta Mouse | \$445,500.33 |

> These results were independently verified by computing totals directly from the CSV before documentation. The revenue column is numerically consistent with `units_sold × unit_price` across all 5,000 rows (zero mismatches).

---
## Try AURON with the Demo Dataset

Reproduce the analysis demonstrated in our AURON product walkthrough using the exact synthetic sales dataset.

**[⬇ Download the 5,000-row Demo Dataset](examples/auron-demo-sales-5000.csv)**

### Dataset Overview

| Property | Value |
|---|---|
| File | `auron-demo-sales-5000.csv` |
| Records | 5,000 |
| Columns | 7 |
| Products | 8 |
| Categories | 2 |
| Regions | 4 |
| File size | 299,714 bytes |
| Format | CSV (UTF-8) |
| Data type | Synthetic sales transactions |
| Personal information | None |

### Dataset Schema

| Column | Data Type | Description |
|---|---|---|
| `date` | Date | Transaction date (YYYY-MM-DD) |
| `product` | String | Product name |
| `category` | String | Product category |
| `region` | String | Sales region |
| `units_sold` | Integer | Number of units sold |
| `unit_price` | Decimal | Price per unit |
| `revenue` | Decimal | Recorded transaction revenue |

### Dataset Integrity

The SHA-256 checksum below identifies the exact demo CSV distributed with this repository.

```text
071032a26469f8f6543bf8cb22ab3decbba28904aa7ceb95e96fdb96b15b2388
```

### Reproduce the Demo Analysis

1. Open the [AURON Live Application](https://45.77.19.39.sslip.io).
2. Upload `auron-demo-sales-5000.csv`.
3. Enter the following natural-language objective:

```text
What is the total revenue across all regions?
Break down revenue by category and region.
Identify the top-performing product, calculate average
revenue per transaction row, identify any revenue
calculation mismatches, and generate useful visualizations.
Return the key findings and supporting metrics.
```

4. Click **Execute Analysis**.

AURON plans the task, generates Python code, validates the generated code, executes it inside a disposable isolated Docker container, and returns the analytical findings and downloadable artifacts.

### Expected Analysis Results

The following results have been independently calculated from the original demo CSV and can be used to validate the execution.

| Metric | Expected Result |
|---|---:|
| Total Revenue | $44,017,002.80 |
| Transaction Rows | 5,000 |
| Average Revenue per Row | $8,803.40 |
| Top-performing Product | Alpha Laptop |
| Top Product Revenue | $21,220,416.00 |
| Revenue Mismatches | 0 |
| Total Revenue Discrepancy | $0.00 |

### Revenue by Category

| Category | Total Revenue |
|---|---:|
| Electronics | $40,160,712.52 |
| Accessories | $3,856,290.28 |
| **Total** | **$44,017,002.80** |

### Revenue by Region

| Region | Total Revenue |
|---|---:|
| North | $11,605,501.12 |
| East | $11,396,830.28 |
| South | $10,579,567.59 |
| West | $10,435,103.81 |
| **Total** | **$44,017,002.80** |

### Complete Product Revenue Breakdown

The following table ranks all eight products by total generated revenue.

| Rank | Product | Total Revenue |
|---:|---|---:|
| 1 | Alpha Laptop | $21,220,416.00 |
| 2 | Theta Camera | $8,269,341.52 |
| 3 | Zeta Tablet | $6,134,503.50 |
| 4 | Gamma Monitor | $4,536,451.50 |
| 5 | Eta Dock | $1,452,399.85 |
| 6 | Delta Keyboard | $1,122,166.20 |
| 7 | Epsilon Headset | $836,223.90 |
| 8 | Beta Mouse | $445,500.33 |
| | **Total** | **$44,017,002.80** |

### Data Quality Validation

AURON independently recomputes expected revenue for every transaction using:

```python
expected_revenue = units_sold * unit_price
```

It compares the calculated amount with the recorded `revenue` value using a configured tolerance of `0.01`.

For this dataset:

- Rows evaluated: 5,000
- Mismatched rows: 0
- Total absolute discrepancy: $0.00

### Generated Artifacts

A successful execution produces the following downloadable outputs:

| Artifact | Description |
|---|---|
| `chart.png` | Revenue visualizations by category, region, category and region, and top products |
| `metrics.json` | Machine-readable summary, category and region totals, product metrics and mismatch statistics |
| `revenue_mismatches.csv` | Row-level revenue discrepancies, if any; header-only when there are no mismatches |

### Recorded Test Suite Validation

The recorded full validation run completed with:

| Test Result | Value |
|---|---:|
| Tests Passed | 145 |
| Tests Failed | 0 |
| Runtime | 51.22 seconds |

These results are from the recorded AURON acceptance test run, rather than a claim that every future execution will produce identical timing.

---

**Reproducibility note:** The dataset, checksum and expected analytical figures are provided so developers, evaluators and contributors can independently reproduce and inspect the results generated by AURON.

--- 

## Test Suite & Verification

AURON's test suite contains **145 comprehensive tests** spanning API contracts, CSV defenses, AST validation, tamper-evident receipt verification, container containment, resource limits, and Red Team probes:

```bash
/home/auron/venv/bin/pytest tests/ -v
```

```text
======================= 145 passed, 1 warning in 51.22s ========================
```

- **0 failed, 0 skipped**.
- All tests execute and pass natively on Ubuntu 24.04 LTS against real Docker containers.

---

## Security Boundaries & Limitations

Per `AGENTS.md` rules and PRD §10, we make **no claims of "100% security", "zero risk", or "unbreakable isolation"**:
- **Docker Socket Authority:** The host `auron` service user belongs to the `docker` group. Docker socket access is effectively root-equivalent with respect to container orchestration. The host backend is therefore part of the Trusted Computing Base (TCB).
- **Kernel Attack Surface:** Sandbox isolation relies on the Linux kernel cgroups, namespaces, and seccomp subsystem. Zero-day kernel vulnerabilities could theoretically compromise container isolation.
- **Inference Verification:** LLM output is strictly validated via JSON schema and Python AST inspection before execution; however, the sandbox security controls (network isolation, read-only root, non-root UID) provide the primary blast radius boundary.

---

## License

Apache License 2.0. Built for the Vultr Agent Rush Hackathon (November 2026).
