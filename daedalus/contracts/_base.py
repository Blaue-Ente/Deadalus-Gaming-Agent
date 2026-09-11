"""Shared versioning and pydantic config for leaf domain contracts."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

CONTRACT_SCHEMA_VERSION = 1


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
