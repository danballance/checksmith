import tokenize
from pathlib import Path, PurePosixPath

from astcheck.domain.errors import AstcheckError


class LocalSourceRepository:
    def discover(
        self, *, root: Path, sources: tuple[str, ...], exclusions: tuple[str, ...]
    ) -> tuple[Path, ...]:
        selected: set[Path] = set()
        try:
            for source in sources:
                path = root / source
                if not path.exists():
                    raise ValueError(f"source does not exist: {path}")
                self._collect(
                    path=path, root=root, exclusions=exclusions, selected=selected
                )
        except (OSError, ValueError) as error:
            raise AstcheckError(f"Could not discover Python sources: {error}", None) from error
        if not selected:
            raise AstcheckError("Source selection contains no Python files", None)
        return tuple(sorted(selected, key=lambda item: item.relative_to(root).as_posix()))

    def _collect(
        self,
        *,
        path: Path,
        root: Path,
        exclusions: tuple[str, ...],
        selected: set[Path],
    ) -> None:
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if any(
            candidate.full_match(pattern)
            or candidate.full_match(pattern.removesuffix("/**"))
            for pattern in exclusions
            for candidate in (relative, *relative.parents)
        ):
            return
        for ancestor in (path, *path.parents):
            if ancestor == root:
                break
            if ancestor.is_symlink():
                raise ValueError(
                    f"symbolic links must be explicitly excluded: {ancestor}"
                )
        if path.is_dir():
            for child in sorted(path.iterdir()):
                self._collect(
                    path=child, root=root, exclusions=exclusions, selected=selected
                )
        elif path.is_file():
            if path.suffix == ".py":
                selected.add(path)
        else:
            raise ValueError(f"source is not a regular file or directory: {path}")

    def read(self, *, path: Path) -> str:
        try:
            with tokenize.open(path) as source:
                return source.read()
        except (OSError, UnicodeError, SyntaxError, LookupError) as error:
            raise AstcheckError(f"Could not read source {path}: {error}", None) from error
