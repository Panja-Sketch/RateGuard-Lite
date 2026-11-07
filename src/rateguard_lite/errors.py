"""Typed errors and process exit codes (spec 11.11)."""

from __future__ import annotations

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_BLOCKED = 2
EXIT_UNSAFE = 3
EXIT_INTERNAL = 4
EXIT_LIMITED = 5


class RGLError(Exception):
    """Base error: says what failed, where, and what to do next."""

    exit_code = EXIT_INTERNAL

    def __init__(self, what: str, where: str = "", next_step: str = "") -> None:
        self.what, self.where, self.next_step = what, where, next_step
        super().__init__(self.render())

    def render(self) -> str:
        parts = [self.what]
        if self.where:
            parts.append(f"Where: {self.where}")
        if self.next_step:
            parts.append(f"Next: {self.next_step}")
        return " | ".join(parts)


class UnsafeInputError(RGLError):
    exit_code = EXIT_UNSAFE


class ConfigError(RGLError):
    exit_code = EXIT_UNSAFE


class NetworkBlockedError(RGLError):
    exit_code = EXIT_INTERNAL


class NeedsMappingError(RGLError):
    exit_code = EXIT_BLOCKED
