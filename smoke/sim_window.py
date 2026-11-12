"""Window of the channel simulator (PC B only), opened from the Tools bar."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import sim_control as control

COLORS = {"green": "#1e7b34", "red": "#b3261e", "orange": "#a35a00", "gray": "#555555"}


class SimulatorWindow(tk.Toplevel):
    def __init__(self, master, process_command, here: Path, results: Path):
        super().__init__(master)
        self.title("Channel simulator (PC B, UCA-S)")
        self.process_command, self.here, self.results = process_command, here, results
        self.process: subprocess.Popen | None = None
        self.folder: Path | None = None
        self.console = results / "simulator" / "console.txt"
        self.inputs: list[str] = []
        self.outputs: list[str] = []
        pad = {"padx": 6, "pady": 4}
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Label(frame, text=("UCA-S carries A->B on its left channel and B->A on its right channel. "
                               "Start in Transparent mode, run CAL on both PCs, then choose a condition."),
                  wraplength=640).grid(row=0, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(frame, text="Input (UCA-S, from both PCs):").grid(row=1, column=0, sticky="w", **pad)
        self.input_choice = tk.StringVar()
        self.input_box = ttk.Combobox(frame, textvariable=self.input_choice, width=48, state="readonly")
        self.input_box.grid(row=1, column=1, sticky="w")
        ttk.Label(frame, text="Output (UCA-S, to both PCs):").grid(row=2, column=0, sticky="w", **pad)
        self.output_choice = tk.StringVar()
        self.output_box = ttk.Combobox(frame, textvariable=self.output_choice, width=48, state="readonly")
        self.output_box.grid(row=2, column=1, sticky="w")
        ttk.Button(frame, text="Detect devices", command=self._detect).grid(row=1, column=2, **pad)
        ttk.Label(frame, text="Channel:").grid(row=3, column=0, sticky="w", **pad)
        self.mode = tk.StringVar(value=next(iter(control.MODES)))
        ttk.Combobox(frame, textvariable=self.mode, values=list(control.MODES), width=48,
                     state="readonly").grid(row=3, column=1, sticky="w")
        ttk.Label(frame, text="First attempt number (1–30):").grid(row=4, column=0, sticky="w", **pad)
        self.first = tk.IntVar(value=1)
        ttk.Spinbox(frame, from_=1, to=30, textvariable=self.first, width=4, state="readonly").grid(
            row=4, column=1, sticky="w")
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, sticky="w", **pad)
        self.start_button = tk.Button(buttons, text="Start simulator", font=("Segoe UI", 11, "bold"),
                                      bg="#1f5fbf", fg="white", command=self._start, padx=16)
        self.start_button.pack(side="left", padx=4)
        self.stop_button = ttk.Button(buttons, text="Stop simulator", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=4)
        self.reference_label = tk.Label(frame, text="", wraplength=640, justify="left", anchor="w")
        self.reference_label.grid(row=6, column=0, columnspan=3, sticky="w", **pad)
        self.status_label = tk.Label(frame, text="Simulator not running.", font=("Segoe UI", 11, "bold"),
                                     wraplength=640, justify="left", anchor="w", fg=COLORS["gray"])
        self.status_label.grid(row=7, column=0, columnspan=3, sticky="w", **pad)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._show_reference()
        self._detect()
        self.after(1000, self._poll)

    # ------------------------------------------------------------------ devices
    def _detect(self) -> None:
        threading.Thread(target=self._detect_worker, daemon=True).start()

    def _detect_worker(self) -> None:
        try:
            out = subprocess.run(self.process_command(["channel_sim.py", "list"]), cwd=str(self.here),
                                 capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            devices = json.loads(out.stdout.strip().splitlines()[-1])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as error:
            self.after(0, self._set_status, f"Could not list audio devices: {error}", "red")
            return
        self.after(0, self._devices_ready, devices)

    def _devices_ready(self, devices: dict) -> None:
        self.inputs, self.outputs = devices.get("inputs", []), devices.get("outputs", [])
        self.input_box.configure(values=self.inputs)
        self.output_box.configure(values=self.outputs)
        for choice, names in ((self.input_choice, self.inputs), (self.output_choice, self.outputs)):
            if not choice.get():
                guess = [n for n in names if n.upper().startswith("UCA222-S")]
                if guess:
                    choice.set(guess[0])

    # ------------------------------------------------------------------ run
    def running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def _start(self) -> None:
        if self.running():
            return
        if not self.input_choice.get() or not self.output_choice.get():
            messagebox.showwarning("Choose UCA-S", "Choose the simulator's interface (UCA-S) as input and "
                                   "output. Rename it UCA222-S in Windows Sound settings.", parent=self)
            return
        if self.input_choice.get().split(" (")[0] in ("UCA222-A", "UCA222-B"):
            if not messagebox.askyesno("Modem interface selected", "This looks like a modem's interface, not "
                                       "UCA-S. Use it anyway?", parent=self):
                return
        mode = control.MODES[self.mode.get()]
        if mode != "transparent" and control.read_reference(self.results) is None:
            messagebox.showwarning("No reference", control.describe_reference(None)[0], parent=self)
            return
        self.console.parent.mkdir(parents=True, exist_ok=True)
        command = control.run_command(self.input_choice.get(), self.output_choice.get(), mode,
                                      int(self.first.get()))
        handle = open(self.console, "w", encoding="utf-8")
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        self.process = subprocess.Popen(self.process_command(command), cwd=str(self.here), env=env,
                                        stdout=handle, stderr=subprocess.STDOUT,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        handle.close()
        self.folder = None
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._set_status("Starting…", "gray")

    def _stop(self) -> None:
        if not self.running():
            return
        self._set_status("Stopping…", "gray")
        self.update_idletasks()
        control.stop(self.folder, self.process)
        self._poll_once()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._show_reference()

    def _poll_once(self) -> None:
        text = ""
        try:
            text = self.console.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        if self.folder is None:
            self.folder = control.log_folder(text, self.here)
        if self.process and self.process.poll() is not None:
            tail = text.strip().splitlines()[-1:] or ["(no output)"]
            if self.process.returncode not in (0, None):
                self._set_status(f"Simulator stopped (code {self.process.returncode}): {tail[0]}", "red")
            else:
                self._set_status("Simulator stopped. " + control.describe_status(
                    control.read_status(self.folder))[0], "gray")
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.process = None
            self._show_reference()
            return
        if self.running():
            self._set_status(*control.describe_status(control.read_status(self.folder)))

    def _poll(self) -> None:
        self._poll_once()
        self._show_reference()   # the reference may be measured while the simulator runs
        self.after(1000, self._poll)

    def _show_reference(self) -> None:
        text, color = control.describe_reference(control.read_reference(self.results))
        self.reference_label.configure(text=text, fg=COLORS[color])

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.configure(text=text, fg=COLORS.get(color, COLORS["gray"]))

    def _close(self) -> None:
        if self.running() and not messagebox.askyesno(
                "Simulator running", "Stop the simulator and close this window?", parent=self):
            return
        self._stop()
        self.destroy()
