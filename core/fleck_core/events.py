from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .types import Citation


class _Event(BaseModel):
    # defaults are always present on the wire, so TS fields are non-optional
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class SegmentEvent(_Event):
    type: Literal["segment"]
    meeting_id: str
    segment_id: str
    stream: Literal["you", "client"]
    start_ms: int
    end_ms: int
    text: str
    final: bool
    gate: Literal["client", "you", "overlap"] | None


class LlmCall(_Event):
    """A Claude call made for this assist (the live fallback), for the api's llm_calls trace. "discarded": cut
    before its end because a newer answer for the same turn superseded it."""
    purpose: str
    model: str
    request: str  # the only text sent: the question
    response: str | None  # Claude's text, shown or blocked
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    status: Literal["ok", "blocked", "error", "timeout", "discarded"]
    error: str | None = None
    started_at: int | None = None  # ms from the question's end to the request (negative: sent before it ended)
    first_text_at: int | None = None  # ms from the question's end to Claude's first text token


class AssistEvent(_Event):
    """Replacement rule: a later `sentence` event with the same `sentence_index`, or a `done` whose kind is
    `not_in_material`, replaces text already shown (a streamed sentence that failed its checks). Events with the
    same `assist_id` are one card: a newer answer for the same client turn reuses the id and replaces the card, from
    its `thinking` on. Contract for the helper: post each event before asking the stream for the next one; the
    newer stream then holds its `thinking` (up to 0.2 s) until the older one has stopped, so an older event never lands
    after it (decision 104).
    `kind` is also set on a `sentence` whose answer is already final: a brief replay, or the Claude fallback
    ("general": General knowledge, not from our material)."""

    type: Literal["assist"]
    meeting_id: str
    assist_id: str
    segment_id: str
    phase: Literal["thinking", "delta", "sentence", "done"]
    question: str
    text: str = ""
    sentence_index: int | None = None
    kind: Literal["library", "reference", "general", "not_in_material"] | None = None
    from_brief: bool = False
    citations: list[Citation] = []
    first_words_ms: int | None = None
    full_ms: int | None = None
    rejected: list[str] = []
    llm: list[LlmCall] = []  # on `done`: every Claude call made for this assist, shown or not


class MeetingStateEvent(_Event):
    type: Literal["meeting_state"]
    meeting_id: str
    status: str


Event = Annotated[SegmentEvent | AssistEvent | MeetingStateEvent, Field(discriminator="type")]
_adapter = TypeAdapter(Event)


def parse_event(data: dict) -> SegmentEvent | AssistEvent | MeetingStateEvent:
    """Raises pydantic.ValidationError (a ValueError) on unknown type or bad fields."""
    return _adapter.validate_python(data)
