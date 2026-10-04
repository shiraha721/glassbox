"""Shared lifecycle membership values independent of database and transport implementations."""

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
ACTIVE_PROCESSES = frozenset({"starting", "running", "stopping"})
