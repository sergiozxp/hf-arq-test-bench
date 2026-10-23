"""Import the GUI through a fake tkinter module; no display is required."""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "smoke"))


class Widget:
    def __init__(self, *args, **kwargs):
        pass
    def __getattr__(self, name):
        return Widget()
    def __call__(self, *args, **kwargs):
        return Widget()
    def __setitem__(self, key, value):
        pass
    def __getitem__(self, key):
        return None


class Variable:
    def __init__(self, value=None, **kwargs):
        self.value = "" if value is None else value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value
    def trace_add(self, *args, **kwargs):
        pass


class Root(Widget):
    def after(self, *args, **kwargs):
        pass
    def protocol(self, *args, **kwargs):
        pass


tk = types.ModuleType("tkinter")
tk.Tk, tk.StringVar, tk.IntVar = Root, Variable, Variable
tk.TclError = RuntimeError
for name in ("Text", "Label", "Button"):
    setattr(tk, name, Widget)
ttk = types.ModuleType("tkinter.ttk")
for name in ("LabelFrame", "Label", "Radiobutton", "Entry", "Combobox", "Spinbox", "Button", "Frame",
             "Scrollbar", "Progressbar"):
    setattr(ttk, name, Widget)
dialogs = types.ModuleType("tkinter.filedialog")
messages = types.ModuleType("tkinter.messagebox")
for name in ("askopenfilename", "asksaveasfilename"):
    setattr(dialogs, name, lambda *args, **kwargs: "")
for name in ("showwarning", "showinfo", "showerror", "askyesno", "askyesnocancel"):
    setattr(messages, name, lambda *args, **kwargs: True)
tk.ttk, tk.filedialog, tk.messagebox = ttk, dialogs, messages
sys.modules.update({
    "tkinter": tk, "tkinter.ttk": ttk, "tkinter.filedialog": dialogs,
    "tkinter.messagebox": messages,
})

import smoke_gui

window = smoke_gui.ModemSmokeWindow()
assert window.test_name.get() == smoke_gui.logic.SEQUENCE_TEST, "the full sequence is the default"
assert window.repeat_mode is True
window.test_name.set(smoke_gui.logic.H1_TEST)
window._test_changed()
assert window.repeat_mode is False and window.repeat.get() == 1
window.test_name.set(smoke_gui.logic.SEQUENCE_TEST)
window._test_changed()
window.repeat.set(7)
window._test_changed()
assert window.repeat_mode is True and window.repeat.get() == 7, "the start step is kept"
assert window.values["sound_system"].get() == "wasapi"
assert not window.mercury.running()
assert smoke_gui.device_labels([{"name": "X"}, {"name": "X"}, {"name": "Y"}]) == ["X", "X  [2]", "Y"]
assert smoke_gui.MERCURY_DIR.name == "mercury-1.9.19"
assert smoke_gui.process_command(["mercury_smoke_client.py", "send"])[1:3] == [
    "-u", str(ROOT / "smoke" / "mercury_smoke_client.py")
]
smoke_gui.PACKAGED = True
assert smoke_gui.process_command(["mercury_smoke_client.py", "send"]) == [
    sys.executable, "--script", "mercury_smoke_client.py", "send"
]

# Modem switching, the level meter and the VARA status run without errors.
window.values["modem"].set("vara")
window._modem_changed()
window.values["modem"].set("mercury")
window._modem_changed()
window._show_status({"tx_peak_dbfs": -6.0, "snr": 25.0, "sync": True, "direction": "rx",
                     "audio_ok": True, "bitrate": 321})
window._show_status({"tx_peak_dbfs": -0.5, "snr": None, "sync": False, "direction": "tx",
                     "audio_ok": False, "audio_error": "device lost"})
window._show_vara({"registered": False, "exe_sha256": "ab" * 32,
                   "settings": {"input_device": "UCA222-A", "output_device": "UCA222-A"}},
                  [], ["untick updates"], {"ok": True, "needs_confirmation": False, "message": "ok"})
window._show_vara(None, ["VARA.exe not found"], [], None)

# Calibration: refused while Mercury runs or a modem owns the ports; devices come from Mercury's selection.
warnings_shown = []
smoke_gui.messagebox.showwarning = lambda title, text: warnings_shown.append(title)
window.values["role"].set("B")
window.values["mycall"].set("XQ2CG-2")
window.values["peer_call"].set("XQ2CG-1")
window.values["bw"].set("BW500")
window.test_name.set(smoke_gui.logic.CALIBRATION_TEST)
window._test_changed()
window.mercury.running = lambda: True
window._run()
assert warnings_shown[-1] == "Stop Mercury first", warnings_shown
window.mercury.running = lambda: False
smoke_gui.listeners_with_pid = lambda text, ports: {"8300": [{"pid": 99}], "8301": []}
window._run()
assert warnings_shown[-1] == "A modem is running", warnings_shown
smoke_gui.listeners_with_pid = lambda text, ports: {"8300": [], "8301": []}
window._run()
assert warnings_shown[-1] == "Audio interface unknown", warnings_shown
window.vara_info = {"settings": {"input_device": "UCA222-A (USB Audio CODEC )",
                                 "output_device": "UCA222-A (USB Audio CODEC )"}}
assert window.calibration_devices({}) == ("UCA222-A (USB Audio CODEC )", "UCA222-A (USB Audio CODEC )")
window.test_name.set(smoke_gui.logic.SEQUENCE_TEST)
window._test_changed()

# The output of a full sequence: progress per test, a result per test, then the record.
import json
import tempfile
temporary = Path(tempfile.mkdtemp(prefix="hf_arq_gui_"))
for tag in ("E0-1_16B", "E0-1_1KiB"):
    (temporary / "results" / tag).mkdir(parents=True)
    (temporary / "results" / tag / "result.json").write_text(
        json.dumps({"mode": "listen", "tag": tag, "delivered_intact": True}), encoding="utf-8")
(temporary / "results" / "sequences").mkdir()
(temporary / "results" / "sequences" / "seq.json").write_text(json.dumps(
    {"mode": "listen", "steps": [{"tag": "E0-1_1KiB", "exit_code": 0}], "completed_steps": 2,
     "planned_steps": 2, "outcome": "completed"}), encoding="utf-8")
output = "\n".join([
    "=== STEP 1/8: E0-1_16B (RECEIVER) ===", "Result saved to: results/E0-1_16B/result.json",
    "=== STEP 2/8: E0-1_1KiB (RECEIVER) ===", "Result saved to: results/E0-1_1KiB/result.json",
    "Sequence saved to: results/sequences/seq.json"])
smoke_gui.HERE = temporary
smoke_gui.process_command = lambda command: [sys.executable, "-c", f"print({output!r})"]
window._run_commands([["smoke_sequence.py", "listen"]], {"start": "x", "log": str(temporary / "none.jsonl")})
events = []
while not window.event_queue.empty():
    events.append(window.event_queue.get())
kinds = [kind for kind, _ in events if kind != "log"]
assert kinds == ["progress", "summary", "progress", "summary", "summary", "done"], kinds
summaries = [json.loads(data) for kind, data in events if kind == "summary"]
assert [s.get("tag") for s in summaries[:2]] == ["E0-1_16B", "E0-1_1KiB"], "each test is reported as it ends"
assert summaries[2]["outcome"] == "completed", "the sequence record is the last summary"
assert all((temporary / "results" / tag / "level_summary.json").is_file() for tag in ("E0-1_16B", "E0-1_1KiB")), \
    "each test of the sequence gets its own level summary"
print("ALL SIMULATED GUI TESTS PASSED")
