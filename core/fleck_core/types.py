from typing import Literal

from pydantic import BaseModel

SourceKind = Literal["client", "firm", "reference"]


class Passage(BaseModel):
    n: int
    doc_id: str
    chunk_id: str
    text: str
    title: str
    client: str | None
    kind: SourceKind


class Citation(BaseModel):
    doc_id: str
    chunk_id: str
    title: str
    client: str | None
    kind: SourceKind
