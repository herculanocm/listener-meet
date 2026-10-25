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


def test_live_settings_follow_the_spec(monkeypatch):
    """F4 item 10: grupo "Ao vivo" na tabela; vale na próxima gravação, salvo o intervalo do resumo (em uso); Q1: ligado por
    padrão só com 8 núcleos lógicos ou mais."""
    from listener.config import defaults

    assert defaults.LIVE_KEYS == ("live.enabled", "live.names", "live.model", "live.threads", "live.summary_interval_min", "live.in_widget")
    assert all(SPECS[k].phase == "F4" for k in defaults.LIVE_KEYS)
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
