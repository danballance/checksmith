"""Running a versioned Python package through ``uvx``."""

from packaging.requirements import InvalidRequirement, Requirement

from checksmith.runners.base import PackageRunner, RunnerName


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
            raise ValueError(
                f"uvx package must constrain a version, got: {package!r}"
            )

    def build_argv(
        self,
        *,
        package: str,
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[str, ...]:
        return ("uvx", "--from", package, command, *arguments)
