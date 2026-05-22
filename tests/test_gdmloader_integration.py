import os

import pytest

from gdm.distribution import DistributionSystem


@pytest.mark.skipif(
    os.getenv("RUN_GDMLOADER_INTEGRATION") != "1",
    reason="Set RUN_GDMLOADER_INTEGRATION=1 to run network-dependent gdmloader test.",
)
def test_gdmloader_can_download_distribution_system():
    gdmloader_constants = pytest.importorskip("gdmloader.constants")
    gdmloader_source = pytest.importorskip("gdmloader.source")

    loader = gdmloader_source.SystemLoader()
    loader.add_source(gdmloader_constants.GCS_CASE_SOURCE)

    system = loader.load_dataset(
        system_type=DistributionSystem,
        source_name=gdmloader_constants.GCS_CASE_SOURCE.name,
        dataset_name="p5r",
    )

    assert isinstance(system, DistributionSystem)
    assert system.get_source_bus().name
    assert len(system.get_components()) > 0
