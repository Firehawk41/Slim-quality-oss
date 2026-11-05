"""The software validation pack's signature (D-137, ISO 17025 §7.11.2). The host builds the pack (each
module's installed version, its migrations, its step and tool versions, verify, CI's results); a quality
manager signs it, and the signature covers exactly that set of versions: once a module moves (the host bumps
a pin, D-117), the signature is stale and the pack is signed again.
"""

import hashlib
import json
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_quality.domain.actor import QualityActor
from slim_quality.domain.base import Frozen, NotAllowed
from slim_quality.domain.readiness import Rag


def versions_key(versions: dict[str, str]) -> str:
    """A fingerprint of the versions: the same set, in any order, gives the same key."""
    return hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()


class ValidationSignature(Frozen):
    id: int | None = None
    versions: dict[str, str] = Field(min_length=1)  # module → its version (a commit, or what is known)
    pack: dict = {}  # the pack as signed, whole (the host's)
    note: str = Field(min_length=1, max_length=4000)
    signed_by: QualityActor
    signed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _note(self) -> Self:
        if not self.note.strip():
            raise ValueError("say what was checked")
        return self

    @property
    def key(self) -> str:
        return versions_key(self.versions)


def check_signature(signature: ValidationSignature) -> None:
    if not signature.signed_by.is_manager:
        raise NotAllowed("a quality manager signs the validation pack")


def status(
    signatures: list[ValidationSignature], versions: dict[str, str]
) -> tuple[Rag | None, ValidationSignature | None]:
    """GREEN with the signature covering these versions; AMBER with the latest one, for other versions;
    (None, None) if it was never signed. `signatures` newest first."""
    key = versions_key(versions)
    current = next((s for s in signatures if s.key == key), None)
    if current is not None:
        return Rag.GREEN, current
    if signatures:
        return Rag.AMBER, signatures[0]
    return None, None
