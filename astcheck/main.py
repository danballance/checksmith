from astcheck.adapters.driven.analysis.parser import PythonAstParser
from astcheck.adapters.driven.analysis.sources import LocalSourceRepository
from astcheck.adapters.driven.configuration import (
    YamlConfigurationLoader,
    YamlConfigurationRenderer,
    YamlPolicyLoader,
)
from astcheck.adapters.driven.plugins.registry import (
    EntryPointPluginRegistry,
    InstalledPluginCatalog,
)
from astcheck.adapters.driving.cli.application import AstcheckApplication, TyperOutput
from astcheck.adapters.driving.cli.presentation import ReportPresenter
from astcheck.adapters.driving.cli.setup import SetupPrompts, TerminalInput
from astcheck.application.use_cases.analysis import AnalysisService
from astcheck.application.use_cases.configuration import ConfigureService

analysis = AnalysisService(
    configurations=YamlConfigurationLoader(),
    sources=LocalSourceRepository(),
    parser=PythonAstParser(),
    plugins=EntryPointPluginRegistry(catalog=InstalledPluginCatalog()),
)

app = AstcheckApplication(
    analysis=analysis,
    presenter=ReportPresenter(output=TyperOutput()),
    configuration=ConfigureService(
        policies=YamlPolicyLoader(),
        prompts=SetupPrompts(terminal=TerminalInput()),
        validator=analysis,
        renderer=YamlConfigurationRenderer(),
    ),
).build()


if __name__ == "__main__":
    app()
