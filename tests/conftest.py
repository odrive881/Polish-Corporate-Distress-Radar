# Shared fixtures. The package is imported from its installed location (src layout).
import pytest

from distress_radar.parsing.canonical_schema import MappingConfig, load_mapping_config
from distress_radar.parsing.xsd_validation import XsdValidator


@pytest.fixture(scope="session")
def mapping_config() -> MappingConfig:
    return load_mapping_config()


@pytest.fixture(scope="session")
def validator() -> XsdValidator:
    return XsdValidator()
