"""US retail forex regime (CFTC Part 5 / NFA rules for forex dealer members)."""

from functools import lru_cache
from pathlib import Path

import yaml

from lynxguard.core import ParameterSet

from .rules import RULES

REGIME_ID = "forex_us"
PARAMETERS_FILE = Path(__file__).with_name("parameters.yaml")


@lru_cache(maxsize=1)
def load_parameters() -> ParameterSet:
    with PARAMETERS_FILE.open() as fh:
        return ParameterSet(groups=yaml.safe_load(fh))


__all__ = ["REGIME_ID", "RULES", "load_parameters"]
