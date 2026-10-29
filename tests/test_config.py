from listener.capture.recorder import RecordConfig, SourceSpec
from listener.config.defaults import CAPTURE_KEYS, DEFAULTS, HOTKEY_KEYS, LABELS, SPECS, validate_setting
from listener.model import SettingScope


def test_specs_table_is_consistent():
    assert set(DEFAULTS) == set(LABELS) == set(SPECS)
    assert all(s.error(s.default_text) is None for s in SPECS.values())          # todo padrão passa na própria validação
    assert SPECS["capture.chunk_seconds"].scope == SettingScope.NEXT_RECORDING and SPECS["log.level"].scope == "now"
    assert len(CAPTURE_KEYS) == 7 and len(HOTKEY_KEYS) == 6                    # F5: + o do Histórico (U-028)


def test_validation_rejects_values_that_break_recording():
    assert validate_setting("capture.target_rate", "0")
    assert validate_setting("capture.target_rate", "16000") is None
    assert validate_setting("capture.target_rate", "12345")
    assert validate_setting("capture.chunk_seconds", "3600") and validate_setting("capture.chunk_seconds", "60") is None
    assert validate_setting("capture.silence_rms", "-1") and validate_setting("capture.silence_rms", "abc")
    assert validate_setting("capture.echo_corr_min", "5")
    assert validate_setting("log.level", "verbose") and validate_setting("log.level", "debug") is None
    assert validate_setting("chave.desconhecida", "x") is None


def test_record_config_from_settings_falls_back_on_invalid(store):
    store.set_setting("capture.target_rate", "0")
    store.set_setting("capture.chunk_seconds", "30")
    cfg = RecordConfig.from_settings(store, None, SourceSpec("system"))
    assert cfg.target_rate == 16000 and cfg.chunk_seconds == 30.0 and cfg.validate() == []
    store.set_setting("capture.silence_rms", "0.02")
    cfg.apply_live(store)
    assert cfg.silence_rms == 0.02
    assert store.get(SPECS["capture.silence_rms"]) == 0.02 and store.get(SPECS["log.level"]) == "INFO"
    assert RecordConfig(target_rate=0, chunk_seconds=0).validate()


def test_the_settings_log_hides_secrets_names_and_url_credentials(store, caplog):
    """D-059 e R-04: a troca de configuração vai ao log em DEBUG; o glossário (tem nomes) só com o tamanho, o booleano dele
    com o valor, a URL sem usuário e senha (no provedor também) e a chave nunca."""
    from listener.store.settings import safe_url

    with caplog.at_level("DEBUG", logger="listener.store"):
        store.set_setting("pipeline.glossary", "Zuleica, Orçamento")
        store.set_setting("llm.glossary_enabled", "0")
        store.set_setting("llm.proxy_url", "http://joao:s3nh4@proxy.local:8080")
        store.providers.update_provider("gemini", api_key="AIza-SEGREDO", base_url="https://joao:s3nh4@api.local/v1")
    assert "Zuleica" not in caplog.text and "pipeline.glossary: (padrão) -> (texto com 18 caractere(s))" in caplog.text
    assert "llm.glossary_enabled: (padrão) -> 0" in caplog.text
    assert "joao" not in caplog.text and "s3nh4" not in caplog.text and "AIza-SEGREDO" not in caplog.text
    assert "-> http://***@proxy.local:8080" in caplog.text and "base_url = 'https://***@api.local/v1'" in caplog.text
    assert safe_url("http://u:p@[::1]:3128/x?q=1#f") == "http://***@[::1]:3128/x?q=1"
    assert safe_url("http://u:p@proxy:porta") == safe_url("http://u@[::1") == "(URL ilegível)"
    assert safe_url("http://proxy:3128") == "http://proxy:3128" and safe_url("sem@url") == "sem@url"


def test_live_settings_follow_the_spec(monkeypatch):
    """F4 item 10: grupo "Ao vivo" na tabela; vale na próxima gravação, salvo o intervalo do resumo (em uso); Q1: ligado por
    padrão só com 8 núcleos lógicos ou mais."""
    from listener.config import defaults

    assert defaults.LIVE_KEYS == ("live.enabled", "live.names", "live.model", "live.threads", "live.summary_interval_min", "live.in_widget",
                                  "live.partial")
    assert all(SPECS[k].phase == "F4" for k in defaults.LIVE_KEYS[:-1])
    assert SPECS["live.partial"].default is False and SPECS["live.partial"].phase == "F5.6"     # F5.6 (A-21, P-06): desligado
    assert {k for k in defaults.LIVE_KEYS if SPECS[k].scope == SettingScope.NOW} == {"live.summary_interval_min"}
    assert SPECS["live.model"].default == "small" and validate_setting("live.model", "medium")
    assert validate_setting("live.model", "base") is None
    assert SPECS["live.threads"].default == 2 and validate_setting("live.threads", "9") and validate_setting("live.threads", "1") is None
    assert validate_setting("live.summary_interval_min", "-1") and validate_setting("live.summary_interval_min", "61")
    assert validate_setting("live.summary_interval_min", "0") is None and validate_setting("live.summary_interval_min", "60") is None
    assert SPECS["live.names"].default is True and SPECS["live.in_widget"].default is True
    monkeypatch.setattr(defaults.os, "cpu_count", lambda: 8)
    assert defaults._default_live() is True
    monkeypatch.setattr(defaults.os, "cpu_count", lambda: 4)
    assert defaults._default_live() is False
    monkeypatch.setattr(defaults.os, "cpu_count", lambda: None)
    assert defaults._default_live() is False
