# AURON — Autonomous Analysis. Verified Execution.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-hardened%20sandbox-blue.svg)](https://www.docker.com/)
[![Vultr Serverless Inference](https://img.shields.io/badge/vultr-serverless%20inference-blue.svg)](https://www.vultr.com/products/serverless-inference/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.110+-green.svg)](https://fastapi.tiangolo.com/)

An AI data-analysis agent that executes generated Python code inside disposable, cryptographically verified Docker containers on a dedicated Vultr VM, powered by Vultr Serverless Inference.

> **Hackathon Track:** Vultr Agent Rush — Challenge 1: Blast Radius Zero  
> **Source of Truth:** [`docs/PRD.md`](docs/PRD.md) (v3.0)

---

## Live Production Deployment

AURON is deployed on a dedicated Vultr VM in Tokyo:
- **Public URL:** `http://45.77.19.39` (and `https://45.77.19.39`)
- **Infrastructure:** Vultr Dedicated Cloud Compute VM (VX1 Dedicated: 4 vCPU, 32 GB RAM, 240 GB NVMe, Ubuntu 24.04.5 LTS)
- **Reverse Proxy:** Caddy v2.11 with custom security headers and access logging
- **Service Management:** systemd service (`auron.service`) running under dedicated non-root user `auron`
- **Firewall:** UFW active with minimal open ports (22, 80, 443)

---

## Core Value Proposition: Blast Radius Zero

Most AI code-execution systems execute untrusted LLM-generated code directly in subshells or persistent environments. AURON enforces **Blast Radius Zero**:
1. **Total Network Isolation:** Containers run with `--network none`. No data can be exfiltrated; no external resources can be reached.
2. **Read-Only Root Filesystem:** Root filesystem is mounted read-only (`--read-only`). System binaries and libraries cannot be tampered with.
3. **Non-Root Execution:** Code executes as UID `10001:10001`. Container breakout exploits relying on root privileges are neutralized.
4. **All Linux Capabilities Dropped:** `--cap-drop ALL` strips all root capabilities (raw sockets, ptrace, module loading).
5. **Privilege Escalation Prevention:** `--security-opt no-new-privileges` prevents `setuid` binaries from escalating privileges.
6. **Hardware & Resource Limits:** Strict runtime cgroups limits: 512 MB RAM, 64 PIDs, 1.0 CPU quota, 60s hard timeout.
7. **Bounded Execution Storage:** `/out` is mounted as a 20 MB bounded tmpfs (`rw,noexec,nosuid`). Enforced by the Linux kernel during execution.
8. **Cryptographic Containment Card:** Every execution receipt inspects real Docker daemon metadata and reports cryptographic SHA-256 hashes for all inputs, generated code, artifacts, and output receipts.

---

## Architecture

```text
User / Browser (Dashboard)
          │
          ▼  (HTTPS / HTTP)
   [Caddy Reverse Proxy]
          │  (localhost:8000)
   [FastAPI Backend Application]
   ├── SQLite Persistence (Datasets, Tasks, Attempts, Receipts)
   ├── Untrusted CSV Ingestion & Quality Profiling
   ├── 4-Role AI Orchestrator (Planner → Coder → Repairer → Explainer)
   │     │
   │     ▼ (Vultr Serverless Inference API: llama-3.3-70b-instruct-fp8)
   │   LLM Generates Verified Python AST
   │
   └── Trusted Sandbox Controller (SandboxRunner)
         │  (Docker API /var/run/docker.sock)
         ▼
   [Disposable Docker Container (auron-sandbox:latest)]
   ├── UID 10001:10001 | --network none | --read-only
   ├── --cap-drop ALL | --security-opt no-new-privileges
   ├── Read-only bind mount: /data/input.csv
   ├── Read-only bind mount: /app/code.py
   └── Bounded tmpfs: /tmp (64MB) and /out (20MB)
         │
         ▼  (Host tar-stream extraction & verification)
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
|---------|---------------|---------------|
| **Vultr Dedicated Cloud Compute VM** | Production host running backend, Docker daemon, and Caddy | VX1 Dedicated (4 vCPU, 32 GB RAM, 240 GB NVMe), Ubuntu 24.04.5 LTS |
| **Vultr Serverless Inference** | OpenAI-compatible serverless LLM inference for all 4 agent roles | `https://api.vultrinference.com/v1`, Model: `llama-3.3-70b-instruct-fp8` |

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | `GET` | Service status, Docker availability, and inference mode (`live_vultr` or `mock`) |
| `/api/datasets` | `GET` | List all available datasets (including pre-seeded sample data) |
| `/api/datasets` | `POST` | Upload a new CSV file (multipart/form-data, capped at 10 MB, 100k rows, 200 cols) |
| `/api/datasets/{id}` | `GET` | Retrieve dataset schema, column profiling, preview rows, and SHA-256 hash |
| `/api/tasks` | `POST` | Submit an analysis question for a dataset (triggers asynchronous AI orchestration) |
| `/api/tasks` | `GET` | List recent tasks |
| `/api/tasks/{id}` | `GET` | Retrieve full task status, generated plan/code, attempts, artifacts, and Containment Card |
| `/api/tasks/{id}/receipt` | `GET` | Download the canonical, tamper-evident JSON execution receipt |
| `/api/tasks/{id}/artifacts/{name}` | `GET` | Securely download generated charts, tables, or artifacts (path traversal defended) |

---

## Running Locally

### 1. Prerequisites
- Python 3.11+
- Docker Desktop (WSL 2 on Windows or native Linux Docker)

### 2. Setup
```bash
git clone https://github.com/your-repo/auron.git
cd auron
python -m venv venv
# Linux / macOS:
source venv/bin/activate
# Windows:
.\venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 3. Build Sandbox Image
```bash
python scripts/build_sandbox.py
```

### 4. Configure Environment
```bash
cp .env.example .env
# Edit .env with your Vultr Inference API key:
# VULTR_INFERENCE_API_KEY=your_key_here
```

### 5. Run the Server
```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser at `http://127.0.0.1:8000`.

### 6. Run Test Suite
```bash
python -m pytest tests/ -v
```
All 99 tests pass with 0 failures on Linux.

---

## Production Deployment Runbook

See [`docs/deployment.md`](docs/deployment.md) for full server configuration, systemd units, Caddy reverse proxy setup, log rotation, and automated deployment/rollback scripts.

---

## Security Boundaries & Limitations

Per `AGENTS.md` rules and PRD §10, we make **no claims of "100% security" or "zero risk"**:
- **Docker Socket Authority:** The host `auron` service user belongs to the `docker` group. Docker socket access is effectively root-equivalent with respect to container orchestration. The host backend is therefore part of the Trusted Computing Base (TCB).
- **Kernel Attack Surface:** Sandbox isolation relies on the Linux kernel cgroups, namespaces, and seccomp/apparmor subsystem. Zero-day kernel vulnerabilities could theoretically compromise container isolation.
- **Inference Verification:** LLM output is strictly validated via JSON schema and Python AST inspection before execution; however, the sandbox security controls (network isolation, read-only root, non-root UID) provide the primary blast radius boundary.

---

## License

Apache License 2.0. Built for the Vultr Agent Rush Hackathon.
