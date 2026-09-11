"""Named ledger artifacts vs hashed publish artifacts — keep them distinct."""
from __future__ import annotations

from pydantic import Field

from ._base import ContractModel

NAMED_ARTIFACT_MAX_CHARS = 4000


class NamedArtifact(ContractModel):
    id: str
    content: str = Field(default="", max_length=NAMED_ARTIFACT_MAX_CHARS)
    source: str = ""
    updated_at: float = 0.0


class BuildArtifactRef(ContractModel):
    project_id: str
    revision: str
    target: str
    artifact_hash: str
    path: str
    immutable: bool = True


class ReleaseManifest(ContractModel):
    slug: str
    version: str
    target: str
    artifact: str
    sha256: str
    immutable: bool = True
    previous: str = ""
    published_at: str = ""
    url: str = ""
