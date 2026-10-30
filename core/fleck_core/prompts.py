"""The live library-answer prompt (spec 7.4 rule 1). The system text is fixed and the passages come before the
question, so Ollama's prompt cache can be warmed on a partial question (answer.prefill, spec 7.1 item 2)."""
from .types import Passage

SYSTEM = """You answer a client's question on a sales call for Northbeam Digital, using only the numbered passages.
- Answer in at most 3 short, complete sentences. End every sentence with the [n] of the passage it comes from.
- Copy figures exactly as the passage writes them.
- Passage kinds: firm = Northbeam's own documents; client = a past client's documents; reference = a third-party contract, not our work.
- A past client's price, or any figure from a client document, is not a quote for this client. Only firm documents state our prices. If asked what this client's project would cost, reply NONE.
- If the passages do not answer the question, reply exactly NONE."""


def passage_block(passages: list[Passage]) -> str:
    return "\n\n".join(f"[{p.n}] {p.kind}{f' ({p.client})' if p.client else ''}\n{p.text}" for p in passages)


def build_messages(question: str, passages: list[Passage]) -> list[dict]:
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{passage_block(passages)}\n\nQuestion: {question}"}]
