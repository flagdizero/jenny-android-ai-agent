"""Test per il bridge logging stdlib -> loguru (jenny.channels.ws_logging).

Nota: questo modulo non contiene alcun riferimento ad Android/Chaquopy (è un
bridge puramente stdlib-logging -> loguru); i fake di ``tests.support.android``
non sono quindi applicabili qui.

Copre: idempotenza di ``redirect_lib_logging``, disabilitazione della
propagazione, impostazione opzionale del livello dell'handler, instradamento
dei record verso loguru (livello, messaggio formattato, eccezione), e il
fallback a "INFO" per livelli non mappati.
"""

from __future__ import annotations

import logging

import pytest
from loguru import logger

from jenny.channels.ws_logging import _LoguruBridge, redirect_lib_logging


@pytest.fixture()
def std_logger(request: pytest.FixtureRequest):
    """Logger stdlib con nome univoco per test, ripulito a fine test.

    Evita che gli handler installati da un test restino agganciati al
    logger (i logger stdlib sono singleton globali per nome) e inquinino i
    test successivi.
    """
    name = f"test.logging_bridge.{request.node.name}"
    lib_logger = logging.getLogger(name)
    yield lib_logger
    lib_logger.handlers = []
    lib_logger.propagate = True
    lib_logger.setLevel(logging.NOTSET)


# ---------------------------------------------------------------------------
# redirect_lib_logging: wiring dell'handler
# ---------------------------------------------------------------------------


def test_adds_a_loguru_bridge_handler(std_logger: logging.Logger):
    redirect_lib_logging(std_logger.name)

    assert len(std_logger.handlers) == 1
    assert isinstance(std_logger.handlers[0], _LoguruBridge)
    assert std_logger.handlers[0].lib_name == std_logger.name


def test_disables_propagation(std_logger: logging.Logger):
    redirect_lib_logging(std_logger.name)

    assert std_logger.propagate is False


def test_is_idempotent_does_not_add_a_second_handler(std_logger: logging.Logger):
    redirect_lib_logging(std_logger.name)
    first_handler = std_logger.handlers[0]

    redirect_lib_logging(std_logger.name)

    assert len(std_logger.handlers) == 1
    assert std_logger.handlers[0] is first_handler


def test_no_level_leaves_handler_unfiltered(std_logger: logging.Logger):
    # docstring: "quando *level* è None l'handler non filtra".
    redirect_lib_logging(std_logger.name)

    assert std_logger.handlers[0].level == logging.NOTSET


def test_level_is_applied_to_handler_when_given(std_logger: logging.Logger):
    redirect_lib_logging(std_logger.name, level="warning")

    assert std_logger.handlers[0].level == logging.WARNING


def test_invalid_level_string_falls_back_to_warning(std_logger: logging.Logger):
    redirect_lib_logging(std_logger.name, level="not-a-real-level")

    assert std_logger.handlers[0].level == logging.WARNING


# ---------------------------------------------------------------------------
# Instradamento effettivo dei record verso loguru
# ---------------------------------------------------------------------------


def test_emit_routes_message_with_lib_prefix_to_loguru(std_logger: logging.Logger):
    redirect_lib_logging(std_logger.name)
    captured: list[dict] = []
    sink_id = logger.add(lambda m: captured.append(m.record), level="DEBUG")
    try:
        std_logger.warning("hello %s", "world")
    finally:
        logger.remove(sink_id)

    assert len(captured) == 1
    record = captured[0]
    assert record["level"].name == "WARNING"
    assert record["message"] == f"[{std_logger.name}] hello world"


@pytest.mark.parametrize(
    "std_level,expected_loguru_level",
    [
        (logging.DEBUG, "DEBUG"),
        (logging.INFO, "INFO"),
        (logging.WARNING, "WARNING"),
        (logging.ERROR, "ERROR"),
        (logging.CRITICAL, "CRITICAL"),
    ],
)
def test_level_mapping_for_known_stdlib_levels(
    std_logger: logging.Logger, std_level: int, expected_loguru_level: str
):
    redirect_lib_logging(std_logger.name)
    std_logger.setLevel(logging.DEBUG)
    captured: list[dict] = []
    sink_id = logger.add(lambda m: captured.append(m.record), level="DEBUG")
    try:
        std_logger.log(std_level, "plain message")
    finally:
        logger.remove(sink_id)

    assert len(captured) == 1
    assert captured[0]["level"].name == expected_loguru_level


def test_unmapped_levelno_falls_back_to_info():
    # Livello stdlib "custom" (15) che non compare in _LEVEL_MAP: deve
    # degradare pulito a INFO invece di sollevare.
    bridge = _LoguruBridge("custom.lib")
    record = logging.LogRecord(
        name="custom.lib",
        level=15,
        pathname=__file__,
        lineno=1,
        msg="custom level message",
        args=(),
        exc_info=None,
    )
    captured: list[dict] = []
    sink_id = logger.add(lambda m: captured.append(m.record), level="DEBUG")
    try:
        bridge.emit(record)
    finally:
        logger.remove(sink_id)

    assert len(captured) == 1
    assert captured[0]["level"].name == "INFO"
    assert captured[0]["message"] == "[custom.lib] custom level message"


def test_emit_preserves_exception_info(std_logger: logging.Logger):
    redirect_lib_logging(std_logger.name)
    captured: list[dict] = []
    sink_id = logger.add(lambda m: captured.append(m.record), level="DEBUG")
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            std_logger.exception("it failed")
    finally:
        logger.remove(sink_id)

    assert len(captured) == 1
    exc = captured[0]["exception"]
    assert exc is not None
    assert exc.type is ValueError
    assert str(exc.value) == "boom"


def test_emit_without_exception_has_no_exception_recorded(std_logger: logging.Logger):
    redirect_lib_logging(std_logger.name)
    std_logger.setLevel(logging.DEBUG)
    captured: list[dict] = []
    sink_id = logger.add(lambda m: captured.append(m.record), level="DEBUG")
    try:
        std_logger.info("all good")
    finally:
        logger.remove(sink_id)

    assert len(captured) == 1
    assert captured[0]["exception"] is None
