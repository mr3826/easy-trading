# Shadow Operation

Module: `shadow.py` (+ operator guide `shadow-operator-guide.md`) — **IMPLEMENTED, TESTED**.

Shadow execution records *would-submit* decisions without touching any broker:
point-in-time bars -> signals -> hypothetical state -> archived, replay-verifiable shadow
decisions (`ShadowOrchestrator`, `ShadowRunResult`, replay-mismatch detection).

Purpose in the promotion path: an `APPROVED` strategy must accumulate **forward/shadow
evidence** (REQUIRES FORWARD EVIDENCE) feeding the drift monitor before autonomous paper
operation is trusted. Shadow results are archived via `persistence/shadow_archive.py` and
journaled for audit.
