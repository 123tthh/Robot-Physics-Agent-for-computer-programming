"""Failure-code-driven recovery selection."""

from __future__ import annotations


class RecoveryManager:
    def __init__(self, routes: dict[str, str] | None = None) -> None:
        self._routes = dict(routes or {})

    def register(self, failure_code: str, recovery_skill: str) -> None:
        self._routes[failure_code] = recovery_skill

    def recommend(self, failure_code: str | None) -> str | None:
        if failure_code is None:
            return None
        return self._routes.get(failure_code)


def build_default_recovery_manager() -> RecoveryManager:
    return RecoveryManager(
        {
            "ALT_MISSED": "recalibrate_keyboard",
            "TERMINAL_NOT_FOCUSED": "restore_terminal_focus",
            "TEST_FAILED": "request_fix",
            "PRECONDITION_FAILED": "replan",
        }
    )
