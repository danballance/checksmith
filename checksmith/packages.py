import logging
import re
from abc import ABC, abstractmethod
from typing import Final
from urllib.parse import urlsplit

import nodesemver
from packaging.requirements import InvalidRequirement, Requirement

from checksmith.dtos import PackageType

logger = logging.getLogger(__name__)


class Package(ABC):
    """How one kind of package is named, and how it is invoked.

    Implementations are stateless, so one shared instance per type is enough.
    """

    @property
    @abstractmethod
    def name(self) -> PackageType:
        pass

    @abstractmethod
    def validate_package(self, *, package: str | None) -> None:
        pass

    @abstractmethod
    def build_argv(
        self,
        *,
        package: str | None,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Assemble the vector that runs ``command`` out of ``package``."""

    @classmethod
    def from_name(cls, name: str | PackageType) -> Package:
        """Return the one package type that answers to ``name``.

        A ``match`` rather than a mapping lookup: a type checker proves this covers
        every member of :class:`PackageType`, so adding a third type is an error
        reported here at check time rather than a ``KeyError`` in front of a user.
        """
        match name:
            case PackageType.UVX.value:
                return UvxPackage()
            case PackageType.NPX.value:
                return NpxPackage()
            case PackageType.UV.value:
                return UvPackage()
            case PackageType.UVX:
                return UvxPackage()
            case PackageType.NPX:
                return NpxPackage()
            case PackageType.UV:
                return UvPackage()
            case _:
                raise ValueError("Name not recognised:", name)


ANY_VERSION: Final = "*"


class NpxPackage(Package):
    """An npm package, named as ``name@range``."""

    @property
    def name(self) -> PackageType:
        return PackageType.NPX

    def validate_package(self, *, package: str | None) -> None:
        """Reject anything that is not a name at a constrained range.

        The name half is npm's to judge, not Checksmith's: duplicating that
        grammar here only ages against it, and a name npm refuses fails loudly
        on the first run. Nothing is handed to a shell --- a check resolves to
        an argument vector, see :attr:`checksmith.config.Check.argv` --- so an
        odd name is not a hazard either, just a package that will not install.

        ``rpartition`` reads the last ``@`` as the separator, which is right for
        every well-formed specification, scoped or not. Its one weak spot is
        ``@scope/name`` with no range: the scope marker becomes the separator
        and the complaint lands on the range half. It is still rejected.
        """
        if package is None:
            raise ValueError("npx package must name a versioned package, got null")
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
        package: str | None,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        if package is None:
            raise ValueError("npx package must name a versioned package, got null")
        argv = ("npx", "--yes", "--package", package, command, *arguments)
        logger.debug("npx built %s", argv)
        return argv


class UvxPackage(Package):
    """A Python package, named as a PEP 508 requirement."""

    @property
    def name(self) -> PackageType:
        return PackageType.UVX

    def validate_package(self, *, package: str | None) -> None:
        """Require a version constraint or a Git HTTPS URL pinned to a commit.

        ``packaging`` owns the grammar, so extras and markers are accepted for
        versioned requirements and named Git requirements alike. Direct URLs
        must identify a repository and a full commit, without query or fragment
        options; branches and tags may change between runs.
        """
        if package is None:
            raise ValueError("uvx package must name a versioned package, got null")
        try:
            requirement = Requirement(package)
        except InvalidRequirement as error:
            raise ValueError(
                f"uvx package is not a valid Python requirement: {package!r}"
            ) from error
        if requirement.url is not None:
            problem = (
                "uvx URL package must use git+https with a hostname, repository "
                "path and full 40-character hexadecimal commit, without query "
                f"or fragment, got: {package!r}"
            )
            try:
                url = urlsplit(requirement.url)
                valid_url = (
                    url.scheme == "git+https"
                    and url.hostname is not None
                    and not any(character.isspace() for character in url.netloc)
                    and "\\" not in url.netloc
                    and (url.port is None or 1 <= url.port <= 65535)
                    and re.fullmatch(r"/[^@\s\\]+@[0-9a-fA-F]{40}", url.path)
                    is not None
                    and "?" not in requirement.url
                    and "#" not in requirement.url
                )
            except ValueError as error:
                raise ValueError(problem) from error
            if not valid_url:
                raise ValueError(problem)
            logger.debug("uvx accepted %s, name=%s", package, requirement.name)
            return
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
        package: str | None,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        if package is None:
            raise ValueError("uvx package must name a versioned package, got null")
        argv = ("uvx", "--from", package, command, *arguments)
        logger.debug("uvx built %s", argv)
        return argv


class UvPackage(Package):
    """A Python command owned by the application's locked uv project."""

    @property
    def name(self) -> PackageType:
        return PackageType.UV

    def validate_package(self, *, package: str | None) -> None:
        if package is not None:
            raise ValueError("uv package must be null; the project owns its dependencies")

    def build_argv(
        self,
        *,
        package: str | None,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        self.validate_package(package=package)
        argv = ("uv", "run", "--locked", command, *arguments)
        logger.debug("uv built %s", argv)
        return argv
