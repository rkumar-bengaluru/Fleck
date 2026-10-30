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


class BriefItem(BaseModel):
    """A pre-meeting brief answer (Task 20). question_vector is Embedder.embed_query(question) (0.6b)."""
    question: str
    answer: str
    kind: Literal["library", "reference", "general", "not_in_material"]
    citations: list[Citation]
    question_vector: list[float]
