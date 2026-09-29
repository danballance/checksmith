import ast


class AnnotationNormalizer:
    def parse(self, spelling: str) -> ast.expr:
        try:
            return ast.parse(spelling.strip(), mode="eval").body
        except (SyntaxError, ValueError) as error:
            raise ValueError(
                f"Invalid annotation spelling {spelling!r}: {error}"
            ) from error

    def key(self, annotation: ast.expr) -> str:
        expression = annotation
        while isinstance(expression, ast.Constant) and isinstance(
            expression.value, str
        ):
            expression = self.parse(expression.value)
        return ast.dump(expression, annotate_fields=True, include_attributes=False)

    def spelling_key(self, spelling: str) -> str:
        return self.key(self.parse(spelling))
