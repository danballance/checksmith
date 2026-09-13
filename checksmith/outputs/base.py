"""Shared base type for the results of Checksmith CLI operations."""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict
from rich.console import RenderableType

from checksmith.dtos import ExitCode


class CliOutput(BaseModel, ABC):
    """Base type for the result of a Checksmith CLI operation.

    Each operation in ``checksmith-cli.yaml`` declares exactly one output kind,
    and every such kind subclasses this.
    """

    model_config = ConfigDict(frozen=True)

    @property
    @abstractmethod
    def exit_code(self) -> ExitCode:
        """Process exit code implied by this result."""
        raise NotImplementedError

    @abstractmethod
    def __rich__(self) -> RenderableType:
        """Human-readable console rendering.

        Abstract rather than defaulted: without it Rich falls back to a pretty
        repr, so a missing implementation would ship as a cosmetic bug instead of
        failing loudly.
        """
