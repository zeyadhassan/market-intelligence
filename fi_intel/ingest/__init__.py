"""Deterministic ingestion plane.

Ingestion never runs inside an agent turn. Failures are loud: a crashed
job is recoverable via the persisted cursor, while silent data loss is not.
"""
