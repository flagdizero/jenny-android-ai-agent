"""``OpenAICompatProvider._handle_error`` legge il corpo dell'errore una volta.

Prima lo rileggeva a mano (``doc``, ``body``, ``response.text``) e poi
``_error_metadata`` lo rileggeva di nuovo con ``_error_payload``: due letture,
in due ordini diversi. Ora la lettura e' quella della base, passata ai metadati.
"""

from __future__ import annotations

from jenny.providers.openai_compat_provider import OpenAICompatProvider


class _BodyError(Exception):
    def __init__(self) -> None:
        super().__init__("boom")
        self.reads = 0
        self.status_code = 429

    @property
    def body(self) -> dict:
        self.reads += 1
        return {"error": {"type": "rate_limit_error", "code": "rate_limited"}}


def test_the_error_body_is_read_once_and_reaches_both_message_and_metadata() -> None:
    err = _BodyError()
    reply = OpenAICompatProvider._handle_error(err)

    assert err.reads == 1
    assert "rate_limit_error" in reply.content
    assert reply.error_type == "rate_limit_error"
    assert reply.error_code == "rate_limited"
    assert reply.error_status_code == 429
