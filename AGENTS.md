# AGENTS.md — AURON Coding Instructions

**Source of truth:** `docs/PRD.md` (v3.0)

## Project

AURON is an AI data-analysis agent that executes generated Python code in
disposable Docker containers on a Vultr VM, using Vultr Serverless
Inference for LLM calls.

## Rules

1. Read `docs/PRD.md` before substantial work.
2. Work in small vertical slices. Test before reporting.
3. Never fabricate execution output, receipts, metrics, or security outcomes.
4. Never commit API keys, SSH keys, passwords, or real user data.
5. Never mount Docker socket into sandbox containers.
6. Never use `--privileged`, host networking, or broad host mounts.
7. Never let model output modify sandbox flags, mounts, limits, or environment.
8. Use subprocess argument arrays, not shell-string concatenation.
9. Validate all paths and artifact names — defend against traversal and symlinks.
10. Report actual test results including failures and skips.
11. Never claim "zero risk", "unbreakable", or "100% secure".

## Architecture

- Trusted sandbox controller runs on host and owns Docker authority.
- Sandbox containers: `--network none`, `--read-only`, non-root UID 10001,
  `--cap-drop ALL`, `--security-opt no-new-privileges`, bounded CPU/memory/PIDs,
  bounded tmpfs for /tmp and /out, narrow read-only mounts for input and code.
- Receipt assembled by trusted backend code, never by LLM.
- Containment Card populated from actual Docker inspection data, not expected values.

## Current milestone

**Milestone 3 — Production Deployment on Vultr**

Pre-deployment audit → SSH verification → Linux security validation → Production
server setup (Caddy, systemd, UFW) → Live Vultr Serverless Inference validation →
Public HTTPS deployment & live execution verification.

