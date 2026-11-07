# AURON --- Product Requirements Document v3.0

**Status:** Approved hackathon build specification and source of truth\
**Tagline:** Autonomous analysis. Verified execution.\
**Event:** Vultr Agent Rush --- Challenge 1, "Blast Radius Zero"\
**Target submission:** 8 November 2026 (verify exact deadline and
timezone in the official LabLab portal)\
**Prepared:** 4 November 2026\
**Supersedes:** AURON PRD v1.0 and v2.0

> **North-star rule:** A working, honest, publicly reachable demo beats
> unbuilt features. Real execution, containment evidence, receipts,
> documentation, and submission assets outrank optional scope.

## 0. How to use this document

This is the product and engineering source of truth for AURON's
hackathon MVP.

-   Codex must read this document and the repository's `AGENTS.md`
    before substantial work.
-   Work in small vertical slices. Each slice ends with tests and a
    completion report.
-   Do not silently change scope, security policy, architecture, or
    acceptance criteria.
-   If a requirement is infeasible, explain the trade-off and propose
    the smallest safe alternative before changing it.
-   Never claim a feature is implemented until it has been run and
    verified.
-   The model is never trusted to enforce sandbox policy, classify
    security outcomes, or author execution evidence.

**Naming note:** AURON is the working project name. Domain and trademark
clearance are required before commercial launch, not for the hackathon
MVP.

## 1. Executive summary

AURON is a web-based AI data-analysis agent. A user uploads a CSV or
selects a bundled sample, asks a question in plain English, and receives
a structured plan, generated Python code, real execution results,
charts, downloadable artifacts, and an execution receipt.

Generated Python runs in a fresh, restricted Docker container on a Vultr
VM. The trusted backend---not the model---collects exit status, logs,
runtime observations, container configuration, artifact hashes, and
receipt data.

AURON's signature element is the **Containment Card**: a readable
summary of controls actually applied to the execution container,
populated from the trusted controller's run configuration and Docker
inspection data. Values must reflect observed configuration, never
hardcoded green checks.

A separate **Red Team Arena** runs a small, fixed set of bounded probes
against disposable test containers. The controller observes outcomes
from outside the sandbox. A successful probe is reported as a finding,
never disguised as a block.

**Product promise:** Ask a question. Get real analysis. Inspect what ran
and the boundaries around it.

**Demo positioning:** "AURON doesn't ask you to trust its sandbox. It
shows you what ran, what happened, and which boundaries were enforced."

## 2. Problem, users, and value

AI can generate plausible code and explanations without proving that the
code ran, that the result is correct, or that execution was contained.
Users need useful analysis and evidence about how it was produced.

Initial users: - Analysts and operators who need quick answers from CSV
data. - Small teams that need repeatable analysis without writing
Python. - Teams handling internal business data that want execution on
infrastructure they control.

Finance, healthcare, and compliance-heavy organizations are possible
future customer segments, not validated customers or approved compliance
claims.

Hackathon value: - **Useful work:** turn a business question into actual
data analysis. - **Traceability:** inspect code, exit status, artifacts,
and hashes. - **Containment visibility:** inspect the applied sandbox
configuration and test selected boundaries. - **Infrastructure fit:**
use a Vultr VM for orchestration/execution and Vultr Serverless
Inference for model calls.

Do not claim regulatory compliance, customer traction, or measured
performance unless independently established.

## 3. Judging map

  -----------------------------------------------------------------------
  Judging dimension                   AURON evidence
  ----------------------------------- -----------------------------------
  Application of Technology           Vultr Serverless Inference for
                                      planning, code, repair, and
                                      explanation; Vultr VM; real sandbox
                                      execution and receipts

  Business Value                      Concrete CSV analysis workflow,
                                      clear user pain, understandable
                                      outputs, measured time-to-answer if
                                      benchmarked

  Presentation                        Golden-path demo, Containment Card,
                                      execution receipt, architecture,
                                      reliable fallback

  Originality                         Evidence-backed Containment Card
                                      and controlled Red Team Arena with
                                      honest outcomes
  -----------------------------------------------------------------------

Treat all dimensions as important. Do not optimize presentation at the
expense of a functioning product.

## 4. Scope and priorities

### P0 --- must ship

1.  Application and backend deployed on the Vultr VM.
2.  Vultr Serverless Inference integration working from the backend.
3.  CSV upload, validation, preview, and schema.
4.  Bundled sample dataset and **Use sample dataset** action.
5.  Natural-language question and structured plan.
6.  Python code generation and real execution in a disposable Docker
    sandbox.
7.  Results page with a validated chart and downloadable artifact.
8.  Trusted-backend execution receipt with exact code and observed exit
    status.
9.  Containment Card populated from actual container
    configuration/inspection.
10. Sample Runs gallery with honest recorded-run labels.
11. Basic abuse controls: upload limits, concurrency, request rate
    limits, and inference spend/daily call cap.
12. Public GitHub repository, setup documentation, and public HTTPS demo
    URL.

### P1 --- only after P0 is stable

-   Bounded self-healing (maximum two retries after first attempt;
    reduce to one if schedule requires).
-   Red Team Arena with at least two real probes; target three.
-   Live execution timeline (SSE); polling is an acceptable fallback.
-   Reproducibility check only if all required P0/P1 items are complete.

### P2 --- out of scope before submission

gVisor unless already working and validated; Playwright/browser
automation; multi-agent architecture; user accounts, billing, teams;
extra file formats; advanced chart customization; general-purpose shell
or arbitrary code execution.

### Cut list if behind

1.  Drop all P2.
2.  Drop reproducibility.
3.  Reduce Red Team to two probes (network and environment).
4.  Replace SSE with polling.
5.  Reduce retries to one.

Never cut: public Vultr demo, real sandbox execution, receipt, honest
Containment Card, sample dataset path, README, demo video, and LabLab
submission.

## 5. Primary user journey

1.  User opens AURON and chooses **Use sample dataset** or uploads a
    CSV.
2.  Backend validates it, computes preview/schema, and stores the
    original outside the sandbox.
3.  User asks a natural-language question.
4.  Planner call returns a structured JSON plan.
5.  Coder call returns Python source.
6.  Trusted backend validates the response and starts a fresh sandbox
    with fixed policy.
7.  Sandbox executes the code.
8.  Trusted controller collects logs, exit status, timeout/OOM state,
    Docker inspection, resource observations, and output artifacts.
9.  Backend validates artifacts and builds receipt and Containment Card.
10. If enabled and appropriate, a repair call may generate revised code
    and execute it in a new container.
11. Explainer receives captured results and artifact metadata and drafts
    a user-facing explanation.
12. UI shows findings, chart, downloads, receipt, and containment
    evidence.

Task states: `queued`, `planning`, `generating`, `running`, `verifying`,
`retrying`, `completed`, `failed`, `policy_violation`, `infra_error`.

Do not label a task `verified` unless it passes section 10.

## 6. Functional requirements

### FR-01 --- CSV upload and validation

-   Maximum upload size 10 MB; maximum 100,000 rows; maximum 200
    columns. Configure through environment.
-   Validate by parsing; do not trust filename extension or browser MIME
    type.
-   Try UTF-8, then a documented fallback such as Latin-1; report
    detected encoding.
-   Reject malformed, oversized, or unsupported input with a useful
    error.
-   Return first 20 rows, column names, inferred types, row count, null
    counts, and basic quality flags.
-   Assign UUID identifiers.
-   Keep original file outside sandbox and preserve its SHA-256 hash.
-   Give sandbox a read-only copy.
-   Never include the full dataset in model prompts. Preview values are
    capped and treated as untrusted data.

Acceptance: valid sample uploads and previews; invalid/oversized files
rejected; original input hash unchanged after execution.

### FR-02 --- Task lifecycle and persistence

Use SQLite for hackathon persistence. Persist tasks, attempts,
artifacts, inference calls, and Red Team runs. Expose task status
through an API. Add SSE if time permits; polling is fallback. Use UUID
task/attempt identifiers. Handle application restart without corrupting
task records.

### FR-03 --- Planning

Planner output schema: - `goal` - `columns_used` - `assumptions` -
`steps` - `expected_artifacts` - `data_limitations`

Put schema and capped preview in the user message, never the system
prompt. Explicitly tell the model that CSV headers/cells are untrusted
data, not instructions. Truncate preview cells. If JSON is invalid,
allow one constrained re-ask, then fail clearly. The plan is advisory
and cannot change execution policy.

### FR-04 --- Code generation

-   Allowed sandbox packages: `pandas`, `numpy`, `matplotlib`, plus
    Python standard library.
-   No runtime package installation.
-   Script reads only `/data/input.csv` and writes only `/out/`.
-   Script prints a concise structured result summary to stdout (JSON
    line or documented schema).
-   Charts saved as PNG under `/out/`.
-   Store exact source code for every attempt.
-   Parse generated source with Python AST as a basic validity check;
    this is not a security boundary.
-   Do not rely on regex blocklists or model instructions as sandbox
    security.

### FR-05 --- Disposable sandbox execution

Create a fresh container for every attempt. Apply a fixed
controller-owned profile. The model cannot set container flags, limits,
mounts, runtime, environment allow-list, or network policy. Capture
inspection before removing the container. Remove it after evidence
collection. Reap orphaned AURON containers on startup and periodically.
Limit concurrent jobs.

### FR-06 --- Results and artifacts

Collect: - Exit code and final container state. - stdout/stderr, each
capped at 256 KB. - Start/end timestamps and wall-clock duration. -
Timeout and OOM indicators. - Configured CPU, memory, PID, timeout, and
storage limits. - Resource measurements where available, with
measurement source and sampling method. - Artifact name, size,
MIME/type, and SHA-256. - Docker image tag and immutable image digest. -
Container inspection JSON.

Serve artifacts only from the task's own output directory. Resolve and
validate paths; reject traversal and symlink escapes. Allow-list
extensions such as `.png`, `.csv`, `.md`, `.json`, `.txt`. Validate PNG
magic bytes and decode; parse CSV before accepting it. Enforce
output-size limits **during execution**, not only after. Do not trust
filenames or MIME types from generated code.

### FR-07 --- Bounded self-healing (P1)

Repair only correctable non-zero exits. Do not automatically retry
timeout, OOM, policy violation, or infrastructure failure. Send previous
code, truncated/redacted stderr, and plan to repair model. Run revised
code in a new container under identical policy. Maximum two retries
after initial attempt; reduce to one if required by schedule. Record
attempts separately. Repair model cannot relax policy.

### FR-08 --- Execution receipt

Receipt is assembled by trusted backend code, never authored/certified
by the LLM.

Include task/attempt IDs; timestamps and duration; exact code per
attempt; exit code, timeout/OOM and final status; redacted stdout/stderr
and truncation indicators; configured sandbox policy; Docker inspection
captured before removal; resource measurements with source/method;
artifact names, sizes, MIME and SHA-256; image tag/digest; retry
history; verification results and warnings.

Compute receipt hash as SHA-256 over canonicalized receipt data,
excluding the hash field itself. This provides tamper-evidence for the
serialized receipt; it is not a digital signature or proof against a
compromised host.

### FR-09 --- Containment Card

Show on every result page. Populate from actual run configuration and
Docker inspection.

  Control                Evidence
  ---------------------- ------------------------------------------------
  Network                `HostConfig.NetworkMode`
  Read-only root         `HostConfig.ReadonlyRootfs`
  User                   `Config.User` and numeric UID validation
  Dropped capabilities   `HostConfig.CapDrop`
  no-new-privileges      `HostConfig.SecurityOpt`
  Memory                 `HostConfig.Memory`
  CPU                    `HostConfig.NanoCpus` or equivalent
  PID limit              `HostConfig.PidsLimit`
  Mounts                 `Mounts`, source scope and access mode
  Environment            `Config.Env` compared with explicit allow-list
  Docker socket          Check `Mounts`
  Timeout                Controller config and observed kill event
  Cleanup                Post-run check that container no longer exists

Rules: - Show actual values, not expected values. - Missing evidence is
`unknown`, never green. - If a required control is absent/misconfigured,
mark `policy_violation` and show a warning. - Distinguish configured
controls from independently observed outcomes. - Do not imply
`docker inspect` proves all kernel-level security properties.

### FR-10 --- Verification

Statuses: `verified`, `completed_unverified`, `failed`,
`policy_violation`, `infra_error`.

A run is `verified` only if: 1. Exit code is zero. 2. No timeout or OOM
occurred. 3. Expected artifacts exist and pass type/size validation. 4.
Sandbox profile matches required policy. 5. Structured result data is
parseable and passes task-specific checks. 6. Displayed numeric claims
are traceable to captured structured output or validated artifacts.

Important: stdout proves only what the process printed, not that the
values are true. Independently recompute or validate important numerical
findings against input data where feasible. If independent validation is
unavailable, label the claim unverified and use `completed_unverified`
where appropriate. Explainer receives captured output and artifact
metadata only. Reproducibility is P1 and reports match/mismatch
honestly.

### FR-11 --- Rate limits and spend controls

Initial configurable defaults: - Per IP: 10 analysis tasks/hour. -
Uploads: 5 per 10 minutes. - Global sandbox concurrency: 2. - Queue
depth: 5. - Daily inference-call cap configurable. - Red Team demo-token
protected and rate-limited.

When limits are reached, show a clear message and keep Sample Runs
available. Never expose inference key to browser. Log latency/token
usage when provider returns them.

### FR-12 --- Sample Runs gallery

P0: - Target three genuine pre-executed sample runs; minimum acceptable
fallback is one complete genuine recorded run. - Label each: **"Recorded
run --- executed on \[date/time\]"**. - Store actual receipt, artifacts,
and evidence. - Separate **Use sample dataset** action starts a live
run. - Never present a recorded run as live. - Use Sample Runs as
fallback if inference or judge's upload fails.

### FR-13 --- Health and operations

`GET /health` reports application version, sandbox image tag/digest,
Docker reachability, inference reachability (briefly cached), and
readiness. Public response must not disclose secrets, environment
variables, internal paths, or detailed infrastructure information.

## 7. Red Team Arena (P1)

Separate from user analysis. Fixed probe library only; no user-supplied
code and no arbitrary target input. Use purpose-built disposable test
containers with the same sandbox profile.

  --------------------------------------------------------------------------
  Probe             Controlled action  Expected control   Controller
                                                          evidence
  ----------------- ------------------ ------------------ ------------------
  Network           Attempt one        `--network none`   Listener logs,
                    connection to                         container network
                    controller-owned                      mode, bounded
                    local test                            result
                    listener                              

  Environment       Inspect            No application     Compare observed
                    environment for    secrets            env with
                    deliberately                          allow-list; never
                    absent canary                         use real secret

  Filesystem        Read harmless      No host mount;     Canary hash,
  (stretch)         unmounted canary   read-only root     error/exit,
                    and write to root                     `ReadonlyRootfs`

  PID (stretch)     Spawn bounded      PID limit          Controller
                    child processes                       sampling and
                                                          container result

  Memory (stretch)  Bounded allocation Memory limit       `OOMKilled` and
                    above limit                           controller
                                                          observations

  Timeout (stretch) Bounded busy loop  Wall-clock timeout Controller kill
                                                          timestamp and
                                                          duration
  --------------------------------------------------------------------------

Classifications: `blocked`, `detected`, `timed_out`, `failed`,
`succeeded`, `inconclusive`.

Classification is controller-side from observed evidence. Model is not
involved. A successful probe is a finding. Incomplete evidence is
inconclusive. Do not turn generic process error into proof of a specific
control.

Safety: - Never target host, other tenants, or unrelated public
systems. - Use only local/controller-owned listeners and harmless
canaries. - Every probe has an external watchdog and strict bounds. -
Controller runs outside tested container. - Red Team disabled by default
in deployed app. - Enable only for hackathon demo with server-side
feature flag, authentication/demo token, and rate limit. - Do not expose
arbitrary probe parameters publicly.

## 8. UX screens

1.  **Home:** AURON identity, tagline, upload, Use sample dataset,
    Sample Runs, Red Team link when enabled.
2.  **Workspace:** preview, schema, quality indicators, question input,
    structured plan.
3.  **Execution monitor:** task state, attempt, code, sanitized output,
    retry indicator.
4.  **Results:** findings, chart, table, downloads, status, Containment
    Card, receipt link.
5.  **Receipt viewer:** evidence fields, receipt hash, JSON download.
6.  **Sample Runs:** recorded examples with execution timestamps.
7.  **Red Team Arena:** fixed probes, safety notice, status, observed
    result, evidence, limitations.

Design: readable, consistent, responsive. Status colors reflect backend
states only. No decorative animation before end-to-end workflow works.
Include loading, empty, error, timeout, and unavailable states.

## 9. Model integration --- Vultr Serverless Inference

-   Base URL: `https://api.vultrinference.com/v1`.
-   Use OpenAI-compatible API.
-   Select model ID from account's live model list and store as
    `MODEL_NAME`.
-   API key exists only in backend configuration.
-   Never send key to browser, logs, receipt, prompts, or sandbox.
-   Initial request timeout target: 45 seconds; bounded transport retry
    and safe error handling.
-   Endpoint/model configurable.

Four roles:

  -----------------------------------------------------------------------
  Role              Input             Output            Validation
  ----------------- ----------------- ----------------- -----------------
  Planner           Question, schema, JSON plan         JSON schema
                    capped preview                      

  Coder             Plan and schema   Python source     Format checks and
                                                        AST parse

  Repairer          Prior code,       Revised Python    Same checks and
                    redacted stderr,                    same sandbox
                    plan                                policy

  Explainer         Question,         Markdown          Claim
                    captured                            traceability and
                    structured                          unverified labels
                    output, artifact                    
                    metadata                            
  -----------------------------------------------------------------------

Use structured JSON mode if supported; otherwise strict schema
validation. Log latency/token counts if available. Model never enforces
policy.

## 10. Technical architecture

### Stack

  Layer               Technology
  ------------------- ------------------------------
  Frontend            Next.js + TypeScript
  Backend             Python + FastAPI
  Inference           Vultr Serverless Inference
  Hosting             Existing Vultr VX1 VM
  OS                  Ubuntu 24.04 LTS x64
  Sandbox             Disposable Docker containers
  Analysis            pandas, numpy, matplotlib
  Persistence         SQLite
  Reverse proxy/TLS   Caddy
  Process manager     systemd
  Source control      GitHub

### Current infrastructure context

Provisioned VM settings visible in the user's Vultr console: - VX1
Dedicated CPU; 4 vCPU; 32 GB RAM; 240 GB local NVMe. - Tokyo, Japan. -
Ubuntu 24.04 LTS x64. - Public IPv4 shown: `45.77.19.39`. - Console
hourly rate shown: approximately USD \$0.193/hour. - Instance status
shown as Running.

Codex must not assume SSH access, firewall, credits, DNS, or inference
credentials are ready. Verify explicitly. Never put credentials or
private keys in this PRD or repository.

Local development is on Windows; production deployment is on the Vultr
Ubuntu VM.

### Architecture flow

``` text
Browser --HTTPS--> Caddy
                    |--> Next.js frontend
                    |--> FastAPI orchestrator (host service)
                           |--> Vultr Serverless Inference
                           |--> SQLite task/receipt store
                           |--> Trusted sandbox controller
                                  |--> Docker Engine
                                         |--> fresh disposable container per attempt
                                              no network; non-root; read-only root;
                                              bounded resources; narrow mounts

Trusted Red Team controller --> separate disposable probe containers
```

### Docker authority model

Docker daemon access is highly privileged; membership in the `docker`
group is effectively root-equivalent.

-   Sandbox controller is a narrow trusted component.
-   Never mount Docker socket into sandbox containers.
-   Never use privileged containers or Docker-in-Docker for untrusted
    code.
-   Document controller Docker authority as part of trusted computing
    base.
-   Model output cannot construct arbitrary Docker command flags.
-   Use subprocess argument arrays, not shell-string concatenation.
-   Validate task IDs, paths, and generated filenames.
-   If a separate broker is infeasible, use a dedicated service account
    and minimal controller API, and explicitly document its Docker
    privilege. Do not call it unprivileged if it has Docker group
    access.

## 11. Sandbox specification

### Image

-   `auron-sandbox:<git-sha>`.
-   Python 3.11 or 3.12 slim.
-   Preinstall only pandas, numpy, matplotlib and dependencies.
-   Non-root UID/GID 10001.
-   No package installation at runtime.
-   Record immutable image digest in each receipt.
-   Build and validate on Vultr VM.

### Baseline per-attempt controls

-   Fresh container.
-   `--network none`
-   `--read-only`
-   `--cap-drop ALL`
-   `--security-opt no-new-privileges`
-   `--user 10001:10001`
-   PID limit 64.
-   Memory 512 MB; memory+swap no greater than configured memory where
    supported.
-   CPU limit 1 CPU.
-   `/tmp` bounded tmpfs, initially 64 MB, `noexec,nosuid`.
-   Input mounted read-only.
-   Code mounted read-only.
-   Output mounted only to task-specific location.
-   Environment limited to explicit non-secret allow-list.
-   Wall-clock timeout 60 seconds, enforced by controller.
-   stdout/stderr capped at 256 KB each.
-   Output cap 20 MB enforced during execution.
-   Concurrency cap 2.

Use Docker create/start/wait/inspect/remove or equivalent so inspection
and OOM data are captured before removal.

### Critical storage requirement

A post-run size check alone does not prevent disk exhaustion during
execution.

Before claiming disk containment, implement either: 1. A genuinely
size-bounded filesystem/quota for task output, validated on target VM;
or 2. A bounded tmpfs output mount if artifact sizes and memory budget
permit.

If neither is implemented, disclose that output storage is not
hard-quota-contained and do not show a green disk-limit claim. Post-run
size validation remains defense in depth.

### Cleanup

-   Remove container after evidence capture.
-   Remove task temporary files under retention policy.
-   Reaper removes only stale `auron-*` containers on startup and
    periodically.
-   Never remove unrelated containers/volumes.
-   Cleanup on timeout, cancellation, and backend exceptions.

### Reference Docker profile

Design reference only; validate mount ownership, tmpfs, limits, and
Docker version on target VM.

``` bash
docker create \
  --name auron-<attempt_id> \
  --network none \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --user 10001:10001 \
  --pids-limit 64 \
  --memory 512m \
  --memory-swap 512m \
  --cpus 1 \
  -e MPLBACKEND=Agg \
  -e MPLCONFIGDIR=/tmp/mpl \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v <task_input>:/data/input.csv:ro \
  -v <task_code>:/app/code.py:ro \
  -v <bounded_task_output>:/out:rw \
  auron-sandbox:<tag> \
  python /app/code.py
```

Never add `--privileged`, host networking, Docker socket mounts, broad
host mounts, or application secrets.

Docker is defense-in-depth, not a guarantee against every
kernel/container escape. gVisor is optional P2, not an MVP prerequisite.

## 12. Threat model and security requirements

  -----------------------------------------------------------------------
  Threat                              Control
  ----------------------------------- -----------------------------------
  Malicious/defective generated code  Fixed sandbox profile; model cannot
                                      change policy

  Prompt injection in CSV             Treat cells/headers as untrusted;
                                      capped preview; sandbox is actual
                                      security boundary

  Secret exposure                     Backend-only secrets; no secrets in
                                      sandbox; log redaction

  Host/Docker access                  No socket/privileged mode; narrow
                                      mounts; trusted controller
                                      documented

  Network exfiltration                `--network none`

  Resource exhaustion                 CPU, memory, PID, time, output,
                                      concurrency limits

  Malformed uploads                   Parsing and size/row/column caps

  Path traversal                      Task-scoped paths, resolved-path
                                      checks, symlink defenses

  Public abuse/credit drain           Rate limits, daily inference cap,
                                      Red Team token

  Misleading claims                   Evidence-backed status; unknown
                                      when evidence missing
  -----------------------------------------------------------------------

Allowed claim: "AURON applies these configured controls and reports
observed execution outcomes."

Forbidden claims: "zero risk", "unbreakable", "escape-proof", "100%
secure", or claims that Docker alone guarantees isolation.

## 13. Data model

SQLite minimum: -
`datasets(id, filename, size, sha256, encoding, row_count, schema_json, created_at)` -
`tasks(id, dataset_id, question, status, plan_json, summary_md, created_at, finished_at, receipt_hash)` -
`attempts(id, task_id, idx, code, exit_code, status, stdout, stderr, started_at, ended_at, duration_ms, timed_out, oom_killed, inspect_json, resource_metrics_json)` -
`artifacts(id, attempt_id, name, size, sha256, mime)` -
`inference_calls(id, task_id, role, model, prompt_tokens, completion_tokens, latency_ms, ok)` -
`redteam_runs(id, probe_id, expected, observed, mechanism, classification, started_at, duration_ms, evidence_json)`

Receipts derive from persisted rows and trusted inspection data.

## 14. API surface

``` text
POST /api/datasets
POST /api/datasets/sample
POST /api/tasks
GET  /api/tasks/{id}
GET  /api/tasks/{id}/events
GET  /api/tasks/{id}/receipt
GET  /api/tasks/{id}/artifacts/{name}
GET  /api/samples
POST /api/redteam/{probe_id}/run
GET  /api/redteam/runs
GET  /health
```

Validate all inputs; return safe errors; do not expose secrets or host
internals. Red Team endpoints require feature flag and token.

## 15. Test strategy and acceptance criteria

### Synthetic dataset fixtures

1.  `sales_clean.csv` --- approximately 5,000 rows.
2.  `sales_messy.csv` --- missing values, mixed date formats,
    duplicates.
3.  `latin1.csv` --- non-UTF-8 text.
4.  `large_edge.csv` --- near upload limit.
5.  `adversarial.csv` --- harmless prompt-injection text in a
    cell/header.

Never use real customer or personal data.

### P0 acceptance

-   Valid CSV uploads and previews; invalid/oversized files rejected.
-   Sample dataset path works.
-   Natural-language question produces validated plan.
-   Code is generated and actually executes in disposable container.
-   Suitable analysis produces valid chart and result artifact.
-   Receipt contains exact code, exit status, and artifact hashes.
-   Containment Card derives from actual run config and inspection.
-   Misconfigured control produces warning/policy violation in tests.
-   Public HTTPS demo works from a separate network.
-   Secrets absent from browser bundles, logs, receipts, sandbox
    environment, and Git history.

### Sandbox tests

Where host supports them, test non-root, no network, read-only root,
dropped capabilities, no-new-privileges, PID/CPU/memory/time limits, no
Docker socket, read-only input, output quota, cleanup, timeout cleanup,
artifact traversal, and symlink rejection.

Distinguish tests actually run from tests skipped or unsupported on the
current machine. Docker Desktop on Windows may not reproduce all Linux
host controls; final validation must run on the Ubuntu Vultr VM.

### Verification tests

-   Non-zero exit is never `verified`.
-   Timeout/OOM is never `verified`.
-   Missing/invalid expected artifact is never `verified`.
-   Untraceable numeric claim is labelled unverified.
-   Receipt hash reproducible from canonical data.
-   Sample Runs labelled recorded.

### Red Team tests

-   At least two probes against disposable test containers.
-   Evidence collected outside container.
-   Classification derives from controller evidence.
-   Simulated failed control is reported as `succeeded`/finding or
    `inconclusive`, never falsely `blocked`.

## 16. Deployment and operations

### Local development

-   Develop/test locally on Windows first.
-   Provide documented PowerShell commands.
-   `.env.example` contains placeholders only.
-   `.gitignore` excludes `.env`, keys, certificates, database, uploads,
    and generated artifacts as appropriate.
-   Provide clear local startup instructions.

### Vultr deployment

-   Ubuntu 24.04 LTS VM.
-   SSH key authentication; verify key access before disabling password
    fallback.
-   Firewall inbound: SSH restricted to user's IP where practical, HTTP
    80 and HTTPS 443. Never expose Docker API or database ports.
-   Caddy for HTTPS/reverse proxy.
-   Next.js and FastAPI managed by systemd or documented process
    manager.
-   Secrets in root-owned restrictive file such as `/etc/auron/env`;
    never commit.
-   Dedicated app/service accounts; document Docker privilege of sandbox
    controller.
-   Use stable domain/hostname with valid HTTPS if available.
-   Verify public URL from another network/mobile data.
-   Public health response must not disclose sensitive internals.
-   Back up SQLite, receipts, and important artifacts to durable
    storage; local NVMe is ephemeral.
-   Snapshot after final stable deployment.
-   Include deployment and rollback scripts.
-   Keep VM running during judging if credits permit; monitor billing
    and inference spend.

### Current infrastructure context

Observed in user's Vultr console: - VX1 Dedicated CPU, 4 vCPU, 32 GB
RAM, 240 GB local NVMe. - Tokyo, Japan. - Ubuntu 24.04 LTS x64. - Public
IPv4 displayed: `45.77.19.39`. - Hourly rate displayed: approximately
USD \$0.193/hour. - Instance status displayed as Running.

Codex must verify SSH access, firewall, applied credits, DNS, and
inference credentials; do not assume they are ready. Never put
credentials/private keys in this PRD or repository.

At \$0.193/hour, continuous compute is approximately \$4.63/day,
\$32.42/week, and \$64.85/14 days. These are estimates based on the
displayed rate only; confirm taxes, storage, backups, bandwidth, and
inference charges in Vultr. Do not assume the \$200 credit is applied
until Billing confirms it.

## 17. Delivery plan --- 4 to 8 November

Aggressive schedule. Apply cut list as soon as a checkpoint slips.

  ---------------------------------------------------------------------------
  Date                    Focus                       Exit criterion
  ----------------------- --------------------------- -----------------------
  4 Nov                   Verify                      CLI runs sample script
                          SSH/credits/firewall;       in restricted container
                          secure VM; install Docker;  and emits
                          test Vultr inference; build inspection-backed
                          CLI sandbox runner and      receipt JSON
                          receipt; initialize repo    

  5 Nov                   FastAPI + SQLite; dataset   End-to-end local golden
                          upload/sample;              path
                          plan/code/execute/results   
                          pipeline; minimal Next.js   
                          UI                          

  6 Nov                   Public HTTPS deploy;        P0 complete and
                          Containment Card; receipt   publicly reachable
                          viewer; artifact            
                          validation; rate/spend      
                          controls; Sample Runs       

  7 Nov                   Retry if stable; Red Team   Submission package
                          probes; README,             ready
                          architecture, cover/slides, 
                          demo recording              

  8 Nov                   Bug fixes only; clean       LabLab submission
                          deployment rehearsal;       confirmation captured
                          verify deadline/timezone;   
                          submit                      
  ---------------------------------------------------------------------------

If a checkpoint slips by more than three hours, apply the cut list
rather than adding scope.

## 18. Submission checklist

Verify exact event rules and timezone on the official LabLab page.

-   [ ] Project title and short/long descriptions.
-   [ ] Category and technology tags.
-   [ ] Cover image.
-   [ ] Demo video within length limit.
-   [ ] Slides in accepted format.
-   [ ] Public GitHub repository.
-   [ ] Public application URL live and reachable.
-   [ ] README with setup, architecture, Vultr services, Serverless
    Inference usage, real execution workflow, controls, limitations, and
    deployment.
-   [ ] Demo includes real execution and at least one real containment
    moment.
-   [ ] Recorded Sample Runs clearly labelled.
-   [ ] LabLab submission completed and confirmation saved.
-   [ ] Optional Vultr Community showcase only after required
    submission.

## 19. Four-minute demo script

  -----------------------------------------------------------------------
  Time                                Beat
  ----------------------------------- -----------------------------------
  0:00--0:25                          Problem and AURON promise

  0:25--1:25                          Sample/upload CSV, business
                                      question, plan, generated code,
                                      real execution, chart

  1:25--2:10                          Result, Containment Card, receipt
                                      with code, exit status, timing,
                                      artifact hash

  2:10--3:00                          Network/environment probe,
                                      controller-side evidence, honest
                                      classification

  3:00--3:30                          Vultr architecture: VM, Serverless
                                      Inference, disposable sandbox

  3:30--4:00                          Business value, measured
                                      performance only if benchmarked,
                                      closing line
  -----------------------------------------------------------------------

Record two full takes and a short fallback using Sample Runs. Never
label recorded sample as live. Do not fake blocked probes or performance
numbers.

## 20. Codex operating instructions

Create and maintain `AGENTS.md` at repository root. It contains concise
durable coding instructions and points to `docs/PRD.md` as product
source of truth.

### Codex rules

1.  Read `docs/PRD.md` and `AGENTS.md` before substantial work.
2.  Repository starts from scratch unless inspection proves otherwise.
3.  Work in small vertical slices; each ends with tests and a concise
    report.
4.  Preserve working code; no unnecessary rewrites.
5.  Use plan mode before security-sensitive changes: sandbox manager,
    receipt builder, Containment Card, Red Team controller, deployment.
6.  Never fabricate execution output, receipts, metrics, or security
    outcomes.
7.  Never commit API keys, SSH private keys, passwords, certificates, or
    real user data.
8.  Keep secrets in environment variables or root-owned server
    configuration; provide `.env.example`.
9.  Never mount Docker socket into sandbox; never use privileged mode.
10. Never let model output modify sandbox flags, mounts, limits,
    network, or environment allow-list.
11. Use subprocess argument arrays, not shell-string concatenation.
12. Validate paths and artifact names; defend against traversal and
    symlinks.
13. Run relevant tests and report actual results, including
    failures/skips.
14. Never claim a test passed unless it ran.
15. If a requirement is unsafe/infeasible, explain and propose safer
    alternative.
16. Never claim "zero risk", "unbreakable", "escape-proof", or "100%
    secure".
17. Update README and tests as behavior changes.
18. Commit small logical changes when Git is configured; never push
    secrets.

### First Codex task --- start from zero

Do not build the whole product in one pass.

1.  Inspect project folder and confirm whether repository is empty.
2.  Create initial structure for `sandbox/`, `receipts/`, `tests/`,
    `scripts/`, and `docs/`.
3.  Build sandbox image with baseline controls in section 11.
4.  Implement `run_cli.py`: accept CSV and Python script paths; stage
    task-scoped input/code/output; launch fresh container; capture
    stdout/stderr/exit/timing/timeout/OOM/inspection; compute artifact
    hashes; remove container; print receipt JSON.
5.  Implement cleanup on every failure path.
6.  Add pytest tests for non-root, no network, read-only root,
    PID/memory/time limits where host supports them, output cap, and
    cleanup.
7.  Distinguish tests run from tests unsupported/skipped on current
    machine.
8.  Do not build FastAPI, Next.js, or agent loop in this first task.
9.  Report Windows local commands and identify tests requiring Linux
    Docker/Vultr.
10. Stop and wait for user approval after Milestone 1.

**Important:** Docker Desktop on Windows may not reproduce every Linux
host control. Validate final behavior on Ubuntu Vultr before making
production security claims.

## 21. Definition of done

AURON is ready to submit only when: - Judge can open public HTTPS URL. -
Judge can use sample dataset and complete a real analysis. - Python
executes in a fresh restricted Docker container on Vultr. - Result page
shows real outputs and valid artifact. - Receipt is assembled by trusted
backend and contains actual evidence. - Containment Card reflects
observed configuration, including unknown/failed controls. - At least
one genuine containment test is demonstrated; Red Team Arena included if
stable. - GitHub README explains setup, architecture, Vultr usage,
limitations, deployment. - Demo video and submission assets are
complete. - LabLab submission is confirmed.

**End of AURON PRD v3.0**
