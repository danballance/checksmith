"""The closed set of runners, and the interface every one of them satisfies.

Two questions are asked about a runner, in different places and at different
times: does this string name a package at a constrained version (during schema
validation), and what argument vector runs this command (when a process
specification is built). They are the same polymorphism, so they live on the
same interface rather than as two ``match`` statements in two modules that never
see each other.
"""

from abc import ABC, abstractmethod
from enum import StrEnum


class RunnerName(StrEnum):
    """How a check's package is fetched and executed."""

    UVX = "uvx"
    """A Python package, run through ``uvx``."""

    NPX = "npx"
    """An npm package, run through ``npx``."""


class PackageRunner(ABC):
    """How one kind of package is named, and how it is invoked.

    Implementations are stateless, so one shared instance per runner is enough.
    """

    @property
    @abstractmethod
    def name(self) -> RunnerName:
        """The config value that selects this runner."""

    @abstractmethod
    def validate_package(self, *, package: str) -> None:
        """Raise if ``package`` is not one package at a constrained version.

        A range is a constraint; silence is not. ``ruff>=0.14,<0.15`` says
        something the ecosystem's resolver can honour tomorrow as well as today,
        while a bare ``ruff`` says only "whatever you have", which is the one
        answer a deterministic gate cannot use.

        Raise a plain :class:`ValueError`, never a ``ChecksmithError``. This runs
        inside a Pydantic field validator, which turns a ``ValueError`` into one
        violation among however many others the config file has; anything else
        escapes validation and costs the user the rest of the report.

        Nothing here contacts a registry. Whether ``ruff==0.16.7`` exists is the
        runner's problem; whether it is well formed is Checksmith's.
        """

    @abstractmethod
    def build_argv(
        self,
        *,
        package: str,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Assemble the vector that runs ``command`` out of ``package``.

        ``package`` is passed through exactly as the config file wrote it. Nothing
        here normalises it: running a rewritten specification would mean the
        failing command a user is shown is not the one their config file names.

        Takes strings rather than a resolved check so that this package depends
        on nothing else in Checksmith.
        """
