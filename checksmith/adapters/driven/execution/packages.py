import logging
import re
from abc import ABC, abstractmethod
from urllib.parse import urlsplit

from packaging.requirements import Requirement

from checksmith.domain.config import Check
from checksmith.domain.models import PackageType
from checksmith.domain.packages import NpxPackage, UvPackage, UvxPackage

logger = logging.getLogger(__name__)


class PackageInvocation(ABC):
    @abstractmethod
    def build_argv(
        self,
        *,
        package: str | None,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]: ...

    @classmethod
    def from_name(cls, name: PackageType) -> PackageInvocation:
        match name:
            case PackageType.NPX:
                return NpxInvocation()
            case PackageType.UVX:
                return UvxInvocation()
            case PackageType.UV:
                return UvInvocation()


class NpxInvocation(PackageInvocation):
    def build_argv(
        self,
        *,
        package: str | None,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        NpxPackage().validate_package(package=package)
        assert package is not None
        argv = ("npx", "--yes", "--package", package, command, *arguments)
        logger.debug("npx built %s", argv)
        return argv


class UvxInvocation(PackageInvocation):
    def build_argv(
        self,
        *,
        package: str | None,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        UvxPackage().validate_package(package=package)
        assert package is not None
        requirement = Requirement(package)
        refresh: tuple[str, ...] = ()
        if requirement.url is not None:
            ref = urlsplit(requirement.url).path.rpartition("@")[2]
            if re.fullmatch(r"[0-9a-fA-F]{40}", ref) is None:
                refresh = ("--isolated", "--refresh-package", requirement.name)
        argv = ("uvx", *refresh, "--from", package, command, *arguments)
        logger.debug("uvx built %s", argv)
        return argv


class UvInvocation(PackageInvocation):
    def build_argv(
        self,
        *,
        package: str | None,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        UvPackage().validate_package(package=package)
        argv = ("uv", "run", "--locked", command, *arguments)
        logger.debug("uv built %s", argv)
        return argv


def build_check_argv(check: Check) -> tuple[str, ...]:
    return PackageInvocation.from_name(check.package_type).build_argv(
        package=check.package,
        command=check.command.value,
        arguments=check.arguments,
    )
