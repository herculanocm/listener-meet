"""Padrões das configurações (CP8). scope: 'now' aplica na hora; 'next_recording' só na próxima gravação."""
from __future__ import annotations

DEFAULTS: dict[str, tuple[str, str]] = {
    "capture.chunk_seconds": ("60", "next_recording"),
    "capture.target_rate": ("16000", "next_recording"),
    "capture.mic_rate": ("16000", "next_recording"),
    "capture.silence_rms": ("0.004", "now"),
    "capture.l_alert_seconds": ("15", "now"),
    "capture.r_alert_seconds": ("20", "now"),
    "hotkeys.start": ("ctrl+shift+r", "now"),
    "hotkeys.pause": ("ctrl+shift+p", "now"),
    "hotkeys.stop": ("ctrl+shift+s", "now"),
    "hotkeys.mark": ("ctrl+shift+m", "now"),
    "hotkeys.mute": ("ctrl+shift+u", "now"),
    "ui.widget_pos": ("", "now"),
    "ui.widget_size": ("320x96", "now"),
    "warmup.last_preset_id": ("", "now"),
    "warmup.last_mic_device_id": ("", "now"),
    "warmup.last_mic_device_name": ("", "now"),
    "warmup.last_source_kind": ("system", "now"),
    "warmup.last_source_exe": ("", "now"),
    "warmup.last_in_room": ("0", "now"),
}

LABELS: dict[str, str] = {
    "capture.chunk_seconds": "Duração do pedaço de áudio (s)",
    "capture.target_rate": "Taxa de amostragem gravada (Hz)",
    "capture.mic_rate": "Taxa do microfone (Hz)",
    "capture.silence_rms": "Limiar de silêncio (RMS, 0 a 1)",
    "capture.l_alert_seconds": "Alerta: microfone mudo após (s)",
    "capture.r_alert_seconds": "Alerta: saída muda após (s)",
    "hotkeys.start": "Atalho: iniciar",
    "hotkeys.pause": "Atalho: pausar/retomar",
    "hotkeys.stop": "Atalho: parar",
    "hotkeys.mark": "Atalho: marcador",
    "hotkeys.mute": "Atalho: mudo",
}
