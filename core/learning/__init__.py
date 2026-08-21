"""Cross-tenant machinery for improving the agents from their own traffic.

The corpus itself lives in `db/training.py` (it is a write path, and has to stay
as import-light and failure-proof as the rest of `db/`). What lives here is
everything that *reads* it: the eval harness, and — as they land — the approved
example store and the funnel bandit.

Tenant-specific material (eval cases, rubrics) stays under `apps/<tenant>/`.
"""
