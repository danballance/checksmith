import pytest

from astcheck.domain.configuration import AnalysisPolicy, PluginConfiguration
from astcheck.domain.oopcheck.settings import OopcheckSettings


@pytest.fixture
def analysis_policy() -> AnalysisPolicy:
    settings = OopcheckSettings(
        max_standalone_percent=25,
        min_callable_statements=20,
        min_shared_functions=3,
        collaborators=(),
    )
    return AnalysisPolicy(
        sources=("src",),
        exclusions=(),
        plugins=(
            PluginConfiguration(
                id="oopcheck", settings=settings.model_dump(mode="json")
            ),
        ),
    )
