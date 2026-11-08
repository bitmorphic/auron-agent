<p align="center">
  <img src="assets/banner.png" alt="AURON — Autonomous Analysis. Verified Execution." width="100%">
</p>

# AURON — Autonomous Analysis. Verified Execution.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-hardened%20sandbox-blue.svg)](https://www.docker.com/)
[![Vultr Serverless Inference](https://img.shields.io/badge/vultr-serverless%20inference-blue.svg)](https://www.vultr.com/products/serverless-inference/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.115+-green.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/tests-145%2F145%20passing-brightgreen.svg)](https://github.com/)

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

## Test Suite & Verification

AURON's test suite contains **107 comprehensive tests** spanning API contracts, CSV defenses, AST validation, tamper-evident receipt verification, container containment, resource limits, and Red Team probes:

```bash
/home/auron/venv/bin/pytest tests/ -v
```

```text
======================= 107 passed, 1 warning in 40.85s ========================
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
