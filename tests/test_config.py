from listener.capture.recorder import RecordConfig, SourceSpec
from listener.config.defaults import CAPTURE_KEYS, DEFAULTS, HOTKEY_KEYS, LABELS, SPECS, validate_setting
from listener.model import SettingScope


def test_specs_table_is_consistent():
    assert set(DEFAULTS) == set(LABELS) == set(SPECS)
    assert all(s.error(s.default_text) is None for s in SPECS.values())          # todo padrão passa na própria validação
    assert SPECS["capture.chunk_seconds"].scope == SettingScope.NEXT_RECORDING and SPECS["log.level"].scope == "now"
    assert len(CAPTURE_KEYS) == 7 and len(HOTKEY_KEYS) == 5


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
