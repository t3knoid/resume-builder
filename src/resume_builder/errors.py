"""Error types. Every user-facing problem explains the fix (PRD R10.16)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Problem:
    """One validation problem: where it is, what's wrong, and what a valid value looks like."""

    message: str
    file: str | None = None
    line: int | None = None
    field: str | None = None
    example: str | None = None
    severity: str = "error"  # "error" blocks; "warning" is recorded but does not block

    def render(self) -> str:
        where = self.file or ""
        if self.line:
            where += f", line {self.line}"
        if self.field:
            where += f", field `{self.field}`"
        text = f"{where}: {self.message}" if where else self.message
        if self.example:
            text += f" Example: {self.example}"
        return text

    def to_dict(self) -> dict:
        return {
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "field": self.field,
            "example": self.example,
            "severity": self.severity,
        }


class AppError(Exception):
    """Base class for errors shown to the user."""

    status_code = 400

    def __init__(self, message: str, problems: list[Problem] | None = None):
        super().__init__(message)
        self.message = message
        self.problems = problems or []


class ValidationFailed(AppError):
    status_code = 422


class NotFound(AppError):
    status_code = 404


class PermissionDenied(AppError):
    status_code = 403


class Conflict(AppError):
    status_code = 409


@dataclass
class ProblemList:
    problems: list[Problem] = field(default_factory=list)

    def add(self, message: str, **kw) -> None:
        self.problems.append(Problem(message=message, **kw))

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "error"]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "warning"]

    def raise_if_errors(self, message: str) -> None:
        if self.errors:
            raise ValidationFailed(message, self.problems)
