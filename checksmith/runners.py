import logging
from abc import ABC, abstractmethod
from typing import Final

import nodesemver
from packaging.requirements import InvalidRequirement, Requirement

from checksmith.dtos import RunnerName

logger = logging.getLogger(__name__)


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

    @classmethod
    def from_name(cls, name: str | RunnerName) -> PackageRunner:
        """Return the one runner that answers to ``name``.

        A ``match`` rather than a mapping lookup: a type checker proves this covers
        every member of :class:`RunnerName`, so adding a third runner is an error
        reported here at check time rather than a ``KeyError`` in front of a user.
        """
        match name:
            case RunnerName.UVX.value:
                return UvxRunner()
            case RunnerName.NPX.value:
                return NpxRunner()
            case RunnerName.UVX:
                return UvxRunner()
            case RunnerName.NPX:
                return NpxRunner()
            case _:
                raise ValueError("Name not recognised:", name)


ANY_VERSION: Final = "*"


class NpxRunner(PackageRunner):
    """An npm package, named as ``name@range``."""

    @property
    def name(self) -> RunnerName:
        return RunnerName.NPX

    def validate_package(self, *, package: str) -> None:
        """Reject anything that is not a name at a constrained range.

        The name half is npm's to judge, not Checksmith's: duplicating that
        grammar here only ages against it, and a name npm refuses fails loudly
        on the first run. Nothing is handed to a shell --- see
        :mod:`checksmith.tools.process_spec` --- so an odd name is not a
        hazard either, just a package that will not install.

        ``rpartition`` reads the last ``@`` as the separator, which is right for
        every well-formed specification, scoped or not. Its one weak spot is
        ``@scope/name`` with no range: the scope marker becomes the separator
        and the complaint lands on the range half. It is still rejected.
        """
        _, separator, version_range = package.rpartition("@")
        if separator == "":
            raise ValueError(
                f"npx package must name a version range, as 'name@range', "
                f"got: {package!r}"
            )
        # ``valid_range`` answers ``None`` for any string it cannot read, and
        # raises only for an argument that is not a string at all --- which the
        # signature already rules out. So there is nothing here to catch.
        normalized: str | None = nodesemver.valid_range(version_range, loose=False)
        if normalized is None:
            raise ValueError(
                f"npx package version is not a valid npm range: {version_range!r}"
            )
        if normalized == ANY_VERSION:
            raise ValueError(
                f"npx package version must constrain a version, got: {version_range!r}"
            )
        # The normalised range is the interesting half: what npm will resolve
        # bears little resemblance to what the config file wrote.
        logger.debug(
            "npx accepted %s, range %s -> %s", package, version_range, normalized
        )

    def build_argv(
        self,
        *,
        package: str,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        argv = ("npx", "--yes", "--package", package, command, *arguments)
        logger.debug("npx built %s", argv)
        return argv


class UvxRunner(PackageRunner):
    """A Python package, named as a PEP 508 requirement."""

    @property
    def name(self) -> RunnerName:
        return RunnerName.UVX

    def validate_package(self, *, package: str) -> None:
        """Reject anything that is not a PEP 508 requirement with a version.

        ``packaging`` owns the grammar, so extras and markers are accepted for
        free: whatever ``uvx --from`` understands, so does Checksmith.

        The specifier check is not redundant. PEP 508 makes ``name @ url`` and a
        version specifier mutually exclusive, so requiring a specifier is also
        what rejects ``ruff @ git+https://...`` --- and what rejects
        ``prettier@3.6.2``, which ``packaging`` happily reads as the package
        ``prettier`` at the URL ``3.6.2``.
        """
        try:
            requirement = Requirement(package)
        except InvalidRequirement as error:
            raise ValueError(
                f"uvx package is not a valid Python requirement: {package!r}"
            ) from error
        if len(requirement.specifier) == 0:
            raise ValueError(f"uvx package must constrain a version, got: {package!r}")
        logger.debug(
            "uvx accepted %s, name=%s specifier=%s extras=%s",
            package,
            requirement.name,
            requirement.specifier,
            sorted(requirement.extras),
        )

    def build_argv(
        self,
        *,
        package: str,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        argv = ("uvx", "--from", package, command, *arguments)
        logger.debug("uvx built %s", argv)
        return argv
