import logging
import threading

from listener import log as applog


def test_file_handler_level_and_thread_name(tmp_path):
    applog.setup("INFO")
    path = applog.attach_file(tmp_path / "logs" / "listener.log", "DEBUG")
    lg = applog.get_logger("teste")
    lg.debug("mensagem debug")
    applog.set_level("WARNING")
    lg.info("mensagem info que não entra")
    t = threading.Thread(target=lambda: lg.warning("mensagem warning"), name="thread-x")
    t.start()
    t.join()
    applog.flush()
    text = path.read_text(encoding="utf-8")
    assert "mensagem debug" in text and "mensagem warning" in text and "não entra" not in text
    assert "[thread-x]" in text                                   # %(threadName)s no formato
    assert applog.current_file() == path
    handlers = logging.getLogger(applog.LOGGER_NAME).handlers
    assert [h.name for h in handlers] == [applog.QUEUE_HANDLER]   # só a fila no logger; stderr e arquivo atrás do listener
    applog.detach_file()
    assert applog.current_file() is None


def test_versions_line_and_excepthooks(tmp_path):
    applog.setup("INFO")
    path = applog.attach_file(tmp_path / "l.log")
    applog.log_versions({"teste": "1"})
    assert threading.excepthook is applog.handle_thread_exception     # o pytest-qt troca o sys.excepthook durante o teste
    seen: list[str] = []
    applog.set_crash_notifier(seen.append)
    exc = ValueError("boom")
    applog.handle_exception(type(exc), exc, None)
    applog.flush()
    text = path.read_text(encoding="utf-8")
    assert "listener-meet" in text and "sqlite" in text and "teste 1" in text
    assert "exceção não tratada" in text and "ValueError: boom" in text and seen == ["ValueError: boom"]
    applog.set_crash_notifier(None)
