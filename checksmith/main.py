import logging
import os
from importlib.resources import files

from rich.console import Console
from rich.logging import RichHandler

from checksmith.adapters.driven.astcheck_setup.process import TerminalSetupProcess
from checksmith.adapters.driven.astcheck_setup.setup import AstcheckSetup
from checksmith.adapters.driven.configuration.initialization import YamlConfigRenderer
from checksmith.adapters.driven.configuration.loading import (
    ConfigLoader,
    LocalYamlConfigSource,
)
from checksmith.adapters.driven.execution.import_linter import ImportLinterPrerequisites
from checksmith.adapters.driven.execution.packages import UvInvocation, UvxInvocation
from checksmith.adapters.driven.execution.prerequisites import (
    LocalProjectFiles,
    UvProjectPrerequisites,
)
from checksmith.adapters.driven.execution.processes import (
    LocalProcessRuntime,
    SubprocessExecutor,
)
from checksmith.adapters.driven.execution.pytest import PackagedLauncherSource
from checksmith.adapters.driven.execution.registry import CommandFactory
from checksmith.adapters.driven.filesystem.initialization import (
    LocalInitializationFilesystem,
    PackagedAssetSource,
)
from checksmith.adapters.driving.cli.application import CliApplication
from checksmith.adapters.driving.cli.logs import LOGGER_NAME, LoggingConfigurator
from checksmith.adapters.driving.cli.presentation import OutputPresenter
from checksmith.adapters.driving.cli.setup import InitializationInput, TerminalInput
from checksmith.application.use_cases.checking import CheckService
from checksmith.application.use_cases.initialization import Initializer

app = CliApplication(
    checks=CheckService(
        loader=ConfigLoader(source=LocalYamlConfigSource()),
        commands=CommandFactory(
            executor=SubprocessExecutor(runtime=LocalProcessRuntime()),
            uv_prerequisites=UvProjectPrerequisites(
                environment=os.environ, files=LocalProjectFiles()
            ),
            import_linter_prerequisites=ImportLinterPrerequisites(
                files=LocalProjectFiles()
            ),
            launcher_source=PackagedLauncherSource(
                resource=files("checksmith.adapters.driven.execution")
                / "_pytest_launcher.py"
            ),
            pytest_package=UvInvocation(),
            project_files=LocalProjectFiles(),
        ),
    ),
    initializer=Initializer(
        assets=PackagedAssetSource(
            directory=files("checksmith") / "assets" / "default"
        ),
        renderer=YamlConfigRenderer(),
        filesystem=LocalInitializationFilesystem(),
        astcheck=AstcheckSetup(
            process=TerminalSetupProcess(), commands=UvxInvocation()
        ),
    ),
    terminal=InitializationInput(terminal=TerminalInput()),
    presenter=OutputPresenter(console=Console()),
    logging=LoggingConfigurator(
        logger=logging.getLogger(LOGGER_NAME),
        handler=RichHandler(
            console=Console(stderr=True),
            show_time=True,
            show_level=True,
            show_path=True,
            rich_tracebacks=True,
            markup=False,
        ),
    ),
).build()


if __name__ == "__main__":
    app()
