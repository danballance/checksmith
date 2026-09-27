from astcheck.domain.models import SourceLocation


class AstcheckError(Exception):
    def __init__(self, message: str, location: SourceLocation | None) -> None:
        super().__init__(message)
        self.location = location
