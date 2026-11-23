"""Named pass/fail verification results collected from layers, hooks and the encoder."""
from __future__ import annotations

import logging
from dataclasses import dataclass


@dataclass
class Check:
    """One named pass/fail verification with a human-readable measurement. Tested indirectly by every build test."""

    name: str
    passed: bool
    detail: str


def log_checks(checks: list[Check], log: logging.Logger, prefix: str = "") -> None:
    """Log each check at INFO when it passed and ERROR when it failed. Tested indirectly by test_build.py::test_texture_build_end_to_end."""
    for c in checks:
        (log.info if c.passed else log.error)("  [%s] %s%s: %s", "PASS" if c.passed else "FAIL", prefix, c.name,
                                              c.detail)
