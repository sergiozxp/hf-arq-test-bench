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


class Root(Widget):
    def after(self, *args, **kwargs):
        pass
    def protocol(self, *args, **kwargs):
        pass


tk = types.ModuleType("tkinter")
tk.Tk, tk.StringVar, tk.IntVar = Root, Variable, Variable
for name in ("Text", "Label", "Button"):
    setattr(tk, name, Widget)
ttk = types.ModuleType("tkinter.ttk")
for name in ("LabelFrame", "Label", "Radiobutton", "Entry", "Combobox", "Spinbox", "Button", "Frame", "Scrollbar"):
    setattr(ttk, name, Widget)
dialogs = types.ModuleType("tkinter.filedialog")
messages = types.ModuleType("tkinter.messagebox")
for name in ("askopenfilename", "asksaveasfilename"):
    setattr(dialogs, name, lambda *args, **kwargs: "")
for name in ("showwarning", "showinfo", "showerror", "askyesno"):
    setattr(messages, name, lambda *args, **kwargs: True)
tk.ttk, tk.filedialog, tk.messagebox = ttk, dialogs, messages
sys.modules.update({
    "tkinter": tk, "tkinter.ttk": ttk, "tkinter.filedialog": dialogs,
    "tkinter.messagebox": messages,
})

import smoke_gui

window = smoke_gui.ModemSmokeWindow()
assert window.test_name.get().startswith("E0-1")
assert smoke_gui.process_command(["mercury_smoke_client.py", "send"])[1:3] == [
    "-u", str(ROOT / "smoke" / "mercury_smoke_client.py")
]
smoke_gui.PACKAGED = True
assert smoke_gui.process_command(["mercury_smoke_client.py", "send"]) == [
    sys.executable, "--script", "mercury_smoke_client.py", "send"
]
print("ALL SIMULATED GUI TESTS PASSED")
