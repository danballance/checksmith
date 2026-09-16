"""Running a versioned npm package through ``npx``."""

from typing import Final

import nodesemver

from checksmith.runners.base import PackageRunner, RunnerName

ANY_VERSION: Final = "*"
"""What ``valid_range`` normalises an unconstrained range to.

``''``, ``'*'`` and ``'x'`` all arrive here, because upstream returns
``make_range(...).range or '*'`` rather than an empty string.
"""


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
                f"npx package version must constrain a version, "
                f"got: {version_range!r}"
            )

    def build_argv(
        self,
        *,
        package: str,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        return ("npx", "--yes", "--package", package, command, *arguments)
