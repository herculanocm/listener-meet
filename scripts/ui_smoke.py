"""Verificação das telas da F1 sem monitor (QT_QPA_PLATFORM=offscreen). Desenvolvimento; não é o produto."""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from listener.platform import get_platform  # noqa: E402
from listener.store import Store  # noqa: E402
from listener.ui.controller import Controller  # noqa: E402
from listener.ui.settings_min import SettingsDialog  # noqa: E402
from listener.ui.summary import SummaryDialog  # noqa: E402
from listener.ui.tray import make_icon  # noqa: E402
from listener.ui.warmup import WarmupDialog  # noqa: E402
from listener.ui.widget import RecordingWidget  # noqa: E402
from listener.ui.wizard import FirstRunWizard  # noqa: E402

p = get_platform()
tmp = tempfile.mkdtemp(prefix="listener-ui-")
store = Store(tmp)
store.upsert_me("Teste")
store.seed_default_presets()

wiz = FirstRunWizard(p, None)
wiz.show(); app.processEvents(); wiz.close()
print("wizard ok")
wu = WarmupDialog(p, store)
wu.show(); app.processEvents()
cfg, fields = wu.result_config()
wu.close()
print("warmup ok:", cfg.source.kind, cfg.mic_device.name if cfg.mic_device else None, fields["platform"])
rw = RecordingWidget()
rw.show(); rw.update_state(65, {"L": 0.1, "R": 0.01}, {"L": "ok", "R": "warn"}, False, False, 2); rw.show_alert("teste", True)
app.processEvents(); rw.close()
print("widget ok")
sd = SettingsDialog(store, p); sd.show(); app.processEvents(); sd.close()
print("settings ok")
mid = store.create_meeting("t", "sistema", None, None); store.finish_meeting(mid, 1000)
su = SummaryDialog(store, mid, {"duration_s": 1.0, "warnings": []}); su.show(); app.processEvents(); su.close()
print("summary ok")
make_icon("#e74c3c")
c = Controller(app, p, store)
c.register_hotkeys()
print("hotkeys registrados:", sorted(p.hotkeys()._map))
apps = p.list_playing_apps()
print("apps tocando agora:", [(a.exe, round(a.peak, 3), a.is_meeting_app, a.title[:30]) for a in apps][:8])
print("mic mudo no sistema:", p.mic_mute().get())
p.hotkeys().stop()
store.close()
print("UI OK")
