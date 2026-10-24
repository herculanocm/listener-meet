import logging

from listener import log as applog


def test_file_handler_and_level(tmp_path):
    applog.setup("INFO")
    path = applog.attach_file(tmp_path / "logs" / "listener.log", "DEBUG")
    lg = applog.get_logger("teste")
    lg.debug("mensagem debug")
    applog.set_level("WARNING")
    lg.info("mensagem info que não entra")
    lg.warning("mensagem warning")
    for h in logging.getLogger(applog.LOGGER_NAME).handlers:
        h.flush()
    text = path.read_text(encoding="utf-8")
    assert "mensagem debug" in text and "mensagem warning" in text and "não entra" not in text
    assert applog.current_file() == path
    applog.detach_file()
    assert applog.current_file() is None
