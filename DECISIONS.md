# Decisions (Anti-Drift)

## Local-first architecture
- SCC prioritizes local compute, storage, and execution paths.
- External services are optional and must be explicitly enabled.

## Git is the source of truth
- Git repo is authoritative for configuration and code.
- No server-local hand edits outside version control.

## Frigate detection approach
- Start with **2 cameras**, scale to **10** when stable.
- Frigate recordings are **events-only**.

## LPR roadmap
- Begin with **viability testing** only.
- Full reads likely after ONNX/GPU detector migration.

## Biometrics (face recognition)
- Deferred to **v2+** only.
- Gated behind explicit enablement and policy.

## Assistant policy
- Assistant is **local-first**.
- Web fallback allowed only when enabled and logged.
