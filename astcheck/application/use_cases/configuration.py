from pathlib import Path

from astcheck.application.ports.configuration import (
    ConfigurationRenderer,
    PolicyLoader,
    PolicyPrompts,
    PolicyValidator,
)
from astcheck.domain.errors import AstcheckError


class ConfigureService:
    def __init__(
        self,
        *,
        policies: PolicyLoader,
        prompts: PolicyPrompts,
        validator: PolicyValidator,
        renderer: ConfigurationRenderer,
    ) -> None:
        self._policies = policies
        self._prompts = prompts
        self._validator = validator
        self._renderer = renderer

    def run(
        self, *, project_root: Path, config_file: Path, policy_file: Path | None
    ) -> str:
        for name, path in (
            ("--project-root", project_root),
            ("--config-file", config_file),
            ("--policy", policy_file),
        ):
            if path is not None and not path.is_absolute():
                raise AstcheckError(f"{name} must be an absolute path", None)
        policy = (
            self._policies.load(path=policy_file)
            if policy_file is not None
            else self._prompts.prompt()
        )
        self._validator.validate_policy(policy=policy)
        return self._renderer.render(
            project_root=project_root, config_file=config_file, policy=policy
        )
