"""HF-ARQ Modem Smoke GUI.

Runs the smoke profile MS-1.0 against Mercury 1.9.19 or VARA HF 4.9.0. The GUI
starts Mercury from the official release ZIP (VARA is opened by the operator),
shows Mercury's live levels, checks that the selected modem owns ports 8300 and
8301 before every test, launches the same Python scripts that are available
from a command prompt, and cross-checks a whole session against the other PC's
results ZIP. The scripts, not the GUI, decide whether a test passed.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import smoke_gui_logic as logic

if __name__ == "__main__" and len(sys.argv) >= 3 and sys.argv[1] == "--script":
    sys.exit(logic.run_script(sys.argv[2], sys.argv[3:]))

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import mercury_runtime as runtime
import mercury_smoke_client  # noqa: F401  # included by PyInstaller
import mercury_status
import smoke_cross_check  # noqa: F401  # included by PyInstaller
import smoke_sequence  # noqa: F401  # included by PyInstaller
import bench_calibration  # noqa: F401  # included by PyInstaller
import vara_runtime as vara
from smoke_common import listeners_with_pid, netstat_text, process_name

PACKAGED = bool(getattr(sys, "frozen", False))
COLORS = {"green": "#1b7f3b", "red": "#b3261e", "orange": "#b26a00", "gray": "#555555"}


def work_directory() -> Path:
    """Return a writable directory beside the executable, falling back to Documents."""
    if not PACKAGED:
        return Path(__file__).resolve().parent
    candidates = [
        Path(sys.executable).resolve().parent / "HF-ARQ-Modem-Smoke-data",
        Path.home() / "Documents" / "HF-ARQ-Modem-Smoke-data",
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue
    return candidates[-1]


HERE = work_directory()
RESULTS = HERE / "results"
SETTINGS = HERE / "modem_smoke_settings.json"
MERCURY_DIR = HERE / f"mercury-{runtime.MERCURY_VERSION}"
PORTS = (runtime.CMD_PORT, runtime.DATA_PORT)


def process_command(command: list[str]) -> list[str]:
    """Build the executable or Python invocation for one bundled script."""
    if PACKAGED:
        return [sys.executable, "--script", *command]
    return [console_python(), "-u", str(Path(__file__).resolve().parent / command[0]), *command[1:]]


def console_python() -> str:
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        alternative = executable.with_name("python.exe")
        if alternative.exists():
            return str(alternative)
    return str(executable)


def device_labels(devices: list[dict]) -> list[str]:
    """Return unique display labels; identical names get a numeric suffix."""
    labels, seen = [], {}
    for device in devices:
        name = device["name"]
        seen[name] = seen.get(name, 0) + 1
        labels.append(name if seen[name] == 1 else f"{name}  [{seen[name]}]")
    return labels


class ModemSmokeWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"HF-ARQ Modem Smoke {logic.APP_VERSION}")
        try:
            height = min(980, int(self.winfo_screenheight()) - 80)
        except (TypeError, ValueError, tk.TclError):
            height = 980
        self.geometry(f"1000x{max(600, height)}")
        self.event_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.process: subprocess.Popen | None = None
        self.worker: threading.Thread | None = None
        self.mercury = runtime.MercuryProcess()
        self.repeat_mode: bool | None = None
        self.mercury_exe: str | None = None
        self.zip_check: dict | None = None
        self.devices: dict[str, list[dict]] = {"playback": [], "capture": []}
        self.labels: dict[str, list[str]] = {"playback": [], "capture": []}
        self.session: dict | None = None
        self.session_reported_end = False
        self.vara_info: dict | None = None
        self.status_monitor: mercury_status.StatusMonitor | None = None
        self.test_window: dict | None = None
        self._suggested: tuple[dict | None, dict | None] = (None, None)
        self.values = {
            key: tk.StringVar()
            for key in (
                "role", "mycall", "peer_call", "bw", "mercury_zip",
                "sound_system", "input_id", "output_id", "modem", "vara_exe",
            )
        }
        self.values["role"].set("A")
        self.values["bw"].set("BW500")
        self.values["sound_system"].set("wasapi")
        self.values["modem"].set("mercury")
        self.values["vara_exe"].set(str(vara.DEFAULT_EXE))
        self.input_choice = tk.StringVar()
        self.output_choice = tk.StringVar()
        self._load_settings()
        self._build()
        self.after(100, self._drain_queue)
        self.after(2000, self._watch_mercury)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._modem_changed()
        if self.values["mercury_zip"].get():
            self._prepare_zip(quiet=True)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        pad = dict(padx=8, pady=4)
        station = ttk.LabelFrame(self, text="1. Local station")
        station.pack(fill="x", **pad)
        ttk.Label(station, text="This computer:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Radiobutton(station, text="PC A (sender)", variable=self.values["role"], value="A").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Radiobutton(station, text="PC B (receiver)", variable=self.values["role"], value="B").grid(
            row=0, column=2, sticky="w"
        )
        ttk.Label(station, text="Local callsign:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(station, textvariable=self.values["mycall"], width=18).grid(row=1, column=1, sticky="w")
        ttk.Label(station, text="Peer callsign:").grid(row=1, column=2, sticky="e", **pad)
        ttk.Entry(station, textvariable=self.values["peer_call"], width=18).grid(row=1, column=3, sticky="w")
        ttk.Label(station, text="Bandwidth:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Combobox(
            station, textvariable=self.values["bw"], values=logic.BANDWIDTHS,
            width=10, state="readonly"
        ).grid(row=2, column=1, sticky="w")
        ttk.Label(station, text="Modem:").grid(row=3, column=0, sticky="w", **pad)
        for column, (key, label) in enumerate(logic.MODEMS.items(), start=1):
            ttk.Radiobutton(station, text=label, variable=self.values["modem"], value=key,
                            command=self._modem_changed).grid(row=3, column=column, sticky="w")
        for key in ("role", "mycall", "peer_call"):
            self.values[key].trace_add("write", lambda *_args: self._test_changed())

        self.modem_area = ttk.Frame(self)
        self.modem_area.pack(fill="x")
        modem = ttk.LabelFrame(self.modem_area, text=f"2. Mercury {runtime.MERCURY_VERSION}")
        self.mercury_frame = modem
        ttk.Label(modem, text="Official Mercury ZIP:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(modem, textvariable=self.values["mercury_zip"], width=62).grid(
            row=0, column=1, columnspan=3, sticky="we"
        )
        ttk.Button(modem, text="Browse…", command=self._browse_zip).grid(row=0, column=4, **pad)
        self.zip_status = tk.Label(
            modem, text=f"Select {runtime.OFFICIAL_ZIP_NAME}.", fg=COLORS["gray"], anchor="w",
            justify="left", wraplength=880
        )
        self.zip_status.grid(row=1, column=0, columnspan=5, sticky="w", padx=8)

        ttk.Label(modem, text="Sound system:").grid(row=2, column=0, sticky="w", **pad)
        system_box = ttk.Combobox(
            modem, textvariable=self.values["sound_system"], values=list(runtime.SOUND_SYSTEMS),
            width=10, state="readonly"
        )
        system_box.grid(row=2, column=1, sticky="w")
        system_box.bind("<<ComboboxSelected>>", lambda _event: self._clear_devices())
        self.detect_button = ttk.Button(modem, text="Detect audio devices", command=self._detect_devices)
        self.detect_button.grid(row=2, column=2, sticky="w", **pad)

        ttk.Label(modem, text="Input (signal from the other PC):").grid(row=3, column=0, sticky="w", **pad)
        self.input_box = ttk.Combobox(modem, textvariable=self.input_choice, width=60, state="readonly")
        self.input_box.grid(row=3, column=1, columnspan=3, sticky="we")
        self.input_box.bind("<<ComboboxSelected>>", lambda _event: self._device_chosen("capture"))
        ttk.Label(modem, text="Output (signal to the other PC):").grid(row=4, column=0, sticky="w", **pad)
        self.output_box = ttk.Combobox(modem, textvariable=self.output_choice, width=60, state="readonly")
        self.output_box.grid(row=4, column=1, columnspan=3, sticky="we")
        self.output_box.bind("<<ComboboxSelected>>", lambda _event: self._device_chosen("playback"))
        self.suggestion = tk.Label(modem, text="", fg=COLORS["gray"], anchor="w", justify="left",
                                   wraplength=700)
        self.suggestion.grid(row=5, column=0, columnspan=4, sticky="w", padx=8)
        self.suggest_button = ttk.Button(modem, text="Use suggestion", command=self._use_suggestion,
                                         state="disabled")
        self.suggest_button.grid(row=5, column=4, **pad)
        self.device_warning = tk.Label(modem, text="", fg=COLORS["orange"], anchor="w", justify="left",
                                       wraplength=880)
        self.device_warning.grid(row=6, column=0, columnspan=5, sticky="w", padx=8)

        self.start_mercury_button = ttk.Button(modem, text="Start Mercury", command=self._start_mercury)
        self.start_mercury_button.grid(row=7, column=0, sticky="w", **pad)
        self.stop_mercury_button = ttk.Button(modem, text="Stop Mercury", command=self._stop_mercury,
                                              state="disabled")
        self.stop_mercury_button.grid(row=7, column=1, sticky="w", **pad)
        ttk.Button(modem, text="Check ports 8300/8301", command=self._check_ports).grid(
            row=7, column=2, sticky="w", **pad
        )
        self.mercury_status = tk.Label(modem, text="Mercury is not running.", fg=COLORS["gray"],
                                       font=("Segoe UI", 10, "bold"), anchor="w", justify="left",
                                       wraplength=880)
        self.mercury_status.grid(row=8, column=0, columnspan=5, sticky="w", padx=8, pady=(0, 6))

        ttk.Label(modem, text="Level meter (Mercury):").grid(row=9, column=0, sticky="w", **pad)
        self.peak_bar = ttk.Progressbar(modem, orient="horizontal", length=260, maximum=40)
        self.peak_bar.grid(row=9, column=1, sticky="w")
        self.meter_label = tk.Label(modem, text="Not connected. The meter starts with Mercury.",
                                    fg=COLORS["gray"], anchor="w", justify="left")
        self.meter_label.grid(row=9, column=2, columnspan=3, sticky="w", padx=8)
        self.meter_detail = tk.Label(modem, text="", fg=COLORS["gray"], anchor="w", justify="left",
                                     wraplength=880)
        self.meter_detail.grid(row=10, column=0, columnspan=5, sticky="w", padx=8, pady=(0, 6))

        vframe = ttk.LabelFrame(self.modem_area, text=f"2. VARA HF {vara.VARA_VERSION}")
        self.vara_frame = vframe
        ttk.Label(vframe, text="VARA.exe:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(vframe, textvariable=self.values["vara_exe"], width=62).grid(
            row=0, column=1, columnspan=3, sticky="we")
        ttk.Button(vframe, text="Browse…", command=self._browse_vara).grid(row=0, column=4, **pad)
        ttk.Button(vframe, text="Check VARA", command=self._check_vara).grid(
            row=1, column=0, sticky="w", **pad)
        ttk.Button(vframe, text="Check ports 8300/8301", command=self._check_ports).grid(
            row=1, column=1, sticky="w", **pad)
        self.vara_status = tk.Label(
            vframe, text="Open VARA (Continue to Evaluate if it asks), then press Check VARA.",
            fg=COLORS["gray"], font=("Segoe UI", 10, "bold"), anchor="w", justify="left",
            wraplength=880)
        self.vara_status.grid(row=2, column=0, columnspan=5, sticky="w", padx=8)
        self.vara_detail = tk.Label(vframe, text="", fg=COLORS["orange"], anchor="w", justify="left",
                                    wraplength=880)
        self.vara_detail.grid(row=3, column=0, columnspan=5, sticky="w", padx=8, pady=(0, 6))

        test = ttk.LabelFrame(self, text="3. Smoke test (MS-1.0)")
        test.pack(fill="x", **pad)
        ttk.Label(test, text="Test:").grid(row=0, column=0, sticky="w", **pad)
        self.test_name = tk.StringVar(value=logic.SEQUENCE_TEST)
        ttk.Combobox(
            test, textvariable=self.test_name, values=logic.TEST_CHOICES,
            width=62, state="readonly"
        ).grid(row=0, column=1, columnspan=3, sticky="w")
        self.repeat_label = ttk.Label(test, text=logic.REPETITION_LABEL)
        self.repeat_label.grid(row=1, column=0, sticky="w", **pad)
        self.repeat = tk.IntVar(value=1)
        self.repeat.trace_add("write", lambda *_args: self._test_changed())
        self.repeat_box = ttk.Spinbox(test, from_=1, to=3, textvariable=self.repeat, width=4,
                                      state="readonly")
        self.repeat_box.grid(row=1, column=1, sticky="w")
        self.repeat_hint = tk.Label(test, text=logic.REPETITION_HINT, fg=COLORS["gray"], anchor="w")
        self.repeat_hint.grid(row=1, column=2, columnspan=3, sticky="w", padx=8)
        self.selected_label = tk.Label(test, text="", font=("Segoe UI", 13, "bold"),
                                       fg="#1f5fbf", anchor="w", justify="left", wraplength=900)
        self.selected_label.grid(row=3, column=0, columnspan=5, sticky="w", padx=8, pady=(2, 6))
        self.test_name.trace_add("write", lambda *_args: self._test_changed())
        self._test_changed()
        self.run_button = tk.Button(
            test, text="RUN", font=("Segoe UI", 13, "bold"), bg="#1f5fbf",
            fg="white", command=self._run, padx=24, pady=6
        )
        self.run_button.grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        self.stop_button = ttk.Button(test, text="Stop", command=self._stop, state="disabled")
        self.stop_button.grid(row=2, column=2, sticky="w", **pad)

        tools = ttk.LabelFrame(self, text="Tools")
        # Packed before the result area, at the bottom, so that it stays visible when
        # the screen is shorter than the window (the result log shrinks instead).
        tools.pack(side="bottom", fill="x", **pad)
        ttk.Button(tools, text="Open results folder", command=self._open_results).grid(
            row=0, column=0, padx=4, pady=6
        )
        ttk.Button(tools, text="Create results ZIP", command=self._zip_results).grid(
            row=0, column=1, padx=4, pady=6
        )
        ttk.Button(tools, text="Cross-check with the other PC's ZIP…",
                   command=self._cross_check_zip).grid(row=0, column=2, padx=4, pady=6)
        ttk.Button(tools, text="Cross-check two result files…", command=self._cross_check).grid(
            row=0, column=3, padx=4, pady=6
        )
        ttk.Label(tools, text=f"Working directory: {HERE}", foreground="#555").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=8
        )

        output = ttk.LabelFrame(self, text="4. Result")
        output.pack(fill="both", expand=True, **pad)
        self.summary = tk.Label(
            output, text="Not run.", font=("Segoe UI", 12, "bold"), fg=COLORS["gray"],
            wraplength=900, justify="left", anchor="w"
        )
        self.summary.pack(fill="x", padx=8, pady=6)
        frame = ttk.Frame(output)
        frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.log = tk.Text(frame, height=8, wrap="word", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    # ------------------------------------------------------------------ helpers
    def _form_values(self) -> dict:
        return {key: variable.get() for key, variable in self.values.items()}

    def _load_settings(self) -> None:
        try:
            for key, value in json.loads(SETTINGS.read_text(encoding="utf-8")).items():
                if key in self.values:
                    self.values[key].set(value)
        except (OSError, ValueError):
            pass

    def _save_settings(self) -> None:
        try:
            SETTINGS.write_text(json.dumps(self._form_values(), indent=2), encoding="utf-8")
        except OSError:
            pass

    def _append_log(self, text: str) -> None:
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def _set_summary(self, text: str, color: str) -> None:
        self.summary.configure(text=text, fg=COLORS.get(color, COLORS["gray"]))

    def _set_label(self, label, text: str, color: str) -> None:
        label.configure(text=text, fg=COLORS.get(color, COLORS["gray"]))

    def _in_background(self, function, *args) -> None:
        """Run function in a thread; it reports back through self._call."""
        threading.Thread(target=function, args=args, daemon=True).start()

    def _call(self, function, *args) -> None:
        """Schedule function on the Tk thread (safe from any thread)."""
        self.event_queue.put(("call", (function, args)))

    def _set_busy(self, busy: bool) -> None:
        self.run_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _test_running(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def _test_changed(self) -> None:
        if not hasattr(self, "selected_label"):
            return
        name = self.test_name.get()
        sequence = logic.is_sequence(name)
        enabled = sequence or logic.is_h1(name)
        limit = len(logic.SEQUENCE) if sequence else 3
        if self.repeat_mode != sequence:
            # The spin box is the H1 repetition, or the first step of the sequence.
            self.repeat_mode = sequence
            self.repeat_box.configure(to=limit)
            self.repeat_label.configure(text=logic.SEQUENCE_LABEL if sequence else logic.REPETITION_LABEL)
            self.repeat_hint.configure(text=logic.SEQUENCE_HINT if sequence else logic.REPETITION_HINT)
            self.repeat.set(1)
        self.repeat_box.configure(state="readonly" if enabled else "disabled")
        try:
            repetition = int(self.repeat.get()) if enabled else 1
        except (tk.TclError, ValueError):
            repetition = 1
        repetition = min(max(repetition, 1), limit)
        role = "SENDER (PC A)" if self.values["role"].get() == "A" else "RECEIVER (PC B)"
        modem = logic.MODEMS.get(self.values["modem"].get(), "?")
        order = ""
        if logic.is_calibration(name):
            self.selected_label.configure(
                text=("This PC will run: the bench calibration as " + role + ". Stop Mercury and close VARA "
                      "on both PCs. " + ("Start PC A 5–30 s after PC B shows 'recording'. It plays first, "
                                         "then records (path B->A)." if self.values["role"].get() == "A"
                                         else "Start PC B first: it records (path A->B), then plays.")))
            return
        if sequence:
            order = (" Start PC B first. PC A waits "
                     f"{logic.SEQUENCE_GAP_S} s between tests ({logic.SEQUENCE_GAP_S + 60} s after E0-3)." if self.values["role"].get() == "A"
                     else " Start PC B first; it re-arms after each test.")
        self.selected_label.configure(
            text=f"This PC will run: {logic.test_tag(name, repetition)} as {role} with {modem}. "
                 "The other PC must show the same test." + order)

    def _modem_changed(self) -> None:
        if not hasattr(self, "mercury_frame"):
            return
        mercury = self.values["modem"].get() == "mercury"
        self.mercury_frame.pack_forget()
        self.vara_frame.pack_forget()
        (self.mercury_frame if mercury else self.vara_frame).pack(fill="x", padx=8, pady=4)
        self._test_changed()
        self._save_settings()

    # ------------------------------------------------------------------ Mercury ZIP
    def _browse_zip(self) -> None:
        selected = filedialog.askopenfilename(
            title=f"Select {runtime.OFFICIAL_ZIP_NAME}",
            filetypes=[("Mercury ZIP", "*.zip"), ("All files", "*.*")],
        )
        if selected:
            self.values["mercury_zip"].set(selected)
            self._save_settings()
            self._prepare_zip(quiet=False)

    def _prepare_zip(self, quiet: bool) -> None:
        path = self.values["mercury_zip"].get()
        if not path:
            return
        self._set_label(self.zip_status, "Checking the ZIP SHA-256…", "gray")
        self._in_background(self._prepare_zip_worker, path, quiet)

    def _prepare_zip_worker(self, path: str, quiet: bool) -> None:
        try:
            result = runtime.extract_official(path, MERCURY_DIR)
        except Exception as error:  # noqa: BLE001 - shown to the operator
            self._call(self._zip_failed, str(error), quiet)
            return
        self._call(self._zip_ready, result)

    def _zip_failed(self, message: str, quiet: bool) -> None:
        self.mercury_exe = None
        self.zip_check = None
        self._set_label(self.zip_status, "ZIP NOT ACCEPTED. " + message, "red")
        if not quiet:
            messagebox.showerror("Mercury ZIP", message)

    def _zip_ready(self, result: dict) -> None:
        self.mercury_exe = result["exe"]
        self.zip_check = result["zip"]
        note = "" if result["zip"]["name_matches"] else " (the file was renamed; its content is official)"
        self._set_label(
            self.zip_status,
            f"Official Mercury {runtime.MERCURY_VERSION} ZIP verified (SHA-256 "
            f"{result['zip']['sha256'][:16]}…){note}. mercury.exe ready in {result['folder']}.",
            "green",
        )
        self._detect_devices()

    # ------------------------------------------------------------------ audio devices
    def _clear_devices(self) -> None:
        self.devices = {"playback": [], "capture": []}
        self.labels = {"playback": [], "capture": []}
        self.input_box.configure(values=[])
        self.output_box.configure(values=[])
        self.input_choice.set("")
        self.output_choice.set("")
        self.values["input_id"].set("")
        self.values["output_id"].set("")
        self.suggestion.configure(text="Device ids change with the sound system: detect them again.")
        self.suggest_button.configure(state="disabled")
        self.device_warning.configure(text="")

    def _detect_devices(self) -> None:
        if not self.mercury_exe:
            messagebox.showwarning("Audio devices", "First select and verify the official Mercury ZIP.")
            return
        self.suggestion.configure(text="Asking Mercury for the sound devices…", fg=COLORS["gray"])
        self._in_background(self._detect_worker, self.mercury_exe, self.values["sound_system"].get())

    def _detect_worker(self, exe: str, sound_system: str) -> None:
        try:
            text, devices = runtime.list_devices(exe, sound_system)
        except Exception as error:  # noqa: BLE001 - shown to the operator
            self._call(self._detect_failed, str(error))
            return
        self._call(self._devices_ready, text, devices)

    def _detect_failed(self, message: str) -> None:
        self.suggestion.configure(text="Could not list devices: " + message, fg=COLORS["red"])

    def _devices_ready(self, text: str, devices: dict[str, list[dict]]) -> None:
        self.devices = devices
        self.labels = {kind: device_labels(items) for kind, items in devices.items()}
        self.input_box.configure(values=self.labels["capture"])
        self.output_box.configure(values=self.labels["playback"])
        if not devices["capture"] or not devices["playback"]:
            self.suggestion.configure(text="Mercury listed no devices. Its output:\n" + text[-600:],
                                      fg=COLORS["red"])
            return
        # Restore the operator's previous choice only if that exact id still exists.
        for kind, choice, key in (("capture", self.input_choice, "input_id"),
                                  ("playback", self.output_choice, "output_id")):
            saved = self.values[key].get()
            index = next((i for i, d in enumerate(devices[kind]) if d["id"] == saved), None)
            choice.set(self.labels[kind][index] if index is not None else "")
            if index is None:
                self.values[key].set("")
        self._suggested = (runtime.suggest_device(devices["capture"]),
                           runtime.suggest_device(devices["playback"]))
        if all(self._suggested):
            self.suggestion.configure(
                text=f"Suggested: input '{self._suggested[0]['name']}', output "
                     f"'{self._suggested[1]['name']}'. Confirm that this is the interface "
                     "connected to the other PC.",
                fg=COLORS["gray"],
            )
            self.suggest_button.configure(state="normal")
        else:
            self.suggestion.configure(
                text="No single UCA222 / USB Audio CODEC device found. Select the input and "
                     "output by hand.", fg=COLORS["orange"])
            self.suggest_button.configure(state="disabled")
        self._update_device_warning()

    def _use_suggestion(self) -> None:
        suggested = self._suggested
        if not all(suggested):
            return
        for kind, choice, key, device in (("capture", self.input_choice, "input_id", suggested[0]),
                                          ("playback", self.output_choice, "output_id", suggested[1])):
            index = self.devices[kind].index(device)
            choice.set(self.labels[kind][index])
            self.values[key].set(device["id"])
        self._save_settings()
        self._update_device_warning()

    def _device_chosen(self, kind: str) -> None:
        choice = self.input_choice if kind == "capture" else self.output_choice
        key = "input_id" if kind == "capture" else "output_id"
        label = choice.get()
        if label in self.labels[kind]:
            self.values[key].set(self.devices[kind][self.labels[kind].index(label)]["id"])
        self._save_settings()
        self._update_device_warning()

    def _selected(self, kind: str) -> dict | None:
        key = "input_id" if kind == "capture" else "output_id"
        return runtime.find_device(self.devices[kind], self.values[key].get())

    def _update_device_warning(self) -> None:
        warnings = []
        for kind, word in (("capture", "input"), ("playback", "output")):
            device = self._selected(kind)
            if device and device["system_default"]:
                warnings.append(
                    f"The selected {word} '{device['name']}' is the Windows default {word} device. "
                    "Make another device the default so that Windows sounds are not sent to "
                    "the other PC."
                )
        self.device_warning.configure(text="\n".join(warnings))

    # ------------------------------------------------------------------ Mercury process
    def _start_mercury(self) -> None:
        if self.mercury.running():
            messagebox.showinfo("Mercury", "Mercury is already running from this window.")
            return
        problems = []
        if not self.mercury_exe:
            problems.append("Select and verify the official Mercury ZIP.")
        if not self._selected("capture"):
            problems.append("Select the input device.")
        if not self._selected("playback"):
            problems.append("Select the output device.")
        if problems:
            messagebox.showwarning("Mercury", "\n".join(problems))
            return
        self._save_settings()
        self.start_mercury_button.configure(state="disabled")
        self._set_label(self.mercury_status, "Starting Mercury…", "gray")
        self._in_background(
            self._start_worker, self.mercury_exe, self.values["sound_system"].get(),
            dict(self._selected("capture")), dict(self._selected("playback")),
        )

    def _start_worker(self, exe: str, sound_system: str, capture: dict, playback: dict) -> None:
        try:
            if any(state != "ok" for state in runtime.extracted_state(MERCURY_DIR).values()):
                raise runtime.MercuryError("The extracted Mercury files changed. Select the ZIP again.")
            busy = runtime.busy_ports(listeners_with_pid(
                netstat_text(), (*PORTS, runtime.BROADCAST_PORT, runtime.UI_PORT)))
            if busy:
                raise runtime.MercuryError(
                    f"Ports already in use: {busy} (port: PIDs). Close the other modem program "
                    "(VARA, another Mercury, mercury-ui.exe) first.")
            listing, devices = runtime.list_devices(exe, sound_system)
            for kind, device in (("capture", capture), ("playback", playback)):
                if not runtime.find_device(devices[kind], device["id"]):
                    raise runtime.MercuryError(
                        f"The selected device '{device['name']}' is no longer listed. "
                        "Reconnect it and detect the devices again.")
            version = runtime.version_text(exe)
            session_dir = runtime.new_session_dir(RESULTS)
            ini = session_dir / "mercury.ini"
            ini.write_text(runtime.ini_text(capture["id"], playback["id"], sound_system,
                                            logic.APP_VERSION), encoding="utf-8")
            (session_dir / "audio_devices.txt").write_text(listing, encoding="utf-8")
            log = session_dir / "mercury.log"
            console = session_dir / "mercury_console.txt"
            command = runtime.launch_command(exe, ini, log)
            pid = self.mercury.start(command, session_dir, console)
        except Exception as error:  # noqa: BLE001 - shown to the operator
            if self.mercury.running():
                self.mercury.stop()
            self._call(self._start_failed, str(error))
            return
        session = {
            "launched_by_gui": True, "pid": pid, "sound_system": sound_system,
            "input_name": capture["name"], "input_id": capture["id"],
            "output_name": playback["name"], "output_id": playback["id"],
            "command": command, "version_output": version,
            "zip_sha256": self.zip_check["sha256"] if self.zip_check else "",
            "zip_official": bool(self.zip_check and self.zip_check["official"]),
            "started_utc": runtime.utc_now(), "session_dir": str(session_dir),
            "exe": exe, "ini": str(ini), "log": str(log), "console": str(console),
            "ini_sha256": runtime.sha256_file(ini), "app_version": logic.APP_VERSION,
            "ui_port": runtime.UI_PORT,
            "status_log": str(session_dir / "mercury_status.jsonl"),
        }
        runtime.write_json(session_dir / "session.json", session)
        self._call(self._mercury_started, session)
        # Wait up to 30 s for Mercury to open its TCP ports.
        deadline = time.monotonic() + 30
        verdict = None
        while time.monotonic() < deadline and self.mercury.running():
            verdict = runtime.preflight(listeners_with_pid(netstat_text(), PORTS), pid)
            if verdict["ok"]:
                break
            time.sleep(1)
        self._call(self._mercury_ports_checked, verdict)

    def _start_failed(self, message: str) -> None:
        self.start_mercury_button.configure(state="normal")
        self._set_label(self.mercury_status, "Mercury was not started. " + message, "red")
        messagebox.showerror("Mercury", message)

    def _mercury_started(self, session: dict) -> None:
        self.session = session
        self.session_reported_end = False
        self.stop_mercury_button.configure(state="normal")
        self._append_log(f"Mercury started (PID {session['pid']}): " + " ".join(session["command"]))
        self._append_log(f"Mercury session folder: {session['session_dir']}")
        if runtime.MERCURY_VERSION not in session["version_output"]:
            self._append_log("WARNING: 'mercury.exe -V' did not report version "
                             f"{runtime.MERCURY_VERSION}: {session['version_output'][:200]}")
        self._set_label(self.mercury_status,
                        f"Mercury running (PID {session['pid']}). Waiting for ports 8300/8301…", "gray")

    def _mercury_ports_checked(self, verdict: dict | None) -> None:
        if not self.mercury.running():
            return  # the watcher reports the exit
        if verdict and verdict["ok"]:
            note = (" It listens on all network interfaces: keep this PC off the network "
                    "during the test." if verdict["all_interfaces"] else "")
            self._set_label(self.mercury_status, verdict["message"] + note, "green")
            self._start_monitor()
        else:
            message = verdict["message"] if verdict else "Ports were not checked."
            self._set_label(self.mercury_status,
                            "Mercury is running but the ports are not ready: " + message, "red")

    def _watch_mercury(self) -> None:
        """Report an unexpected Mercury exit; runs every 2 s on the Tk thread."""
        if self.session and not self.mercury.running() and not self.session_reported_end:
            code = self.mercury.exit_code()
            self.mercury.stop()
            self._stop_monitor()
            self._record_session_end("exited", code)
            self.start_mercury_button.configure(state="normal")
            self.stop_mercury_button.configure(state="disabled")
            self._set_label(
                self.mercury_status,
                f"Mercury stopped unexpectedly (exit code {code}). See mercury_console.txt and "
                f"mercury.log in {self.session['session_dir']}.", "red")
            self._append_log(f"Mercury exited with code {code}.")
        monitor = self.status_monitor
        if monitor and not monitor.connected:
            self._set_label(self.meter_label, "Waiting for Mercury's status… "
                            + (f"({monitor.error})" if monitor.error else ""), "gray")
        self.after(2000, self._watch_mercury)

    # ------------------------------------------------------------------ level meter
    def _start_monitor(self) -> None:
        self._stop_monitor()
        if not self.session:
            return
        self.status_monitor = mercury_status.StatusMonitor(
            self.session.get("ui_port", runtime.UI_PORT), log_path=self.session.get("status_log"),
            on_status=lambda status: self._call(self._show_status, status)).start()

    def _stop_monitor(self) -> None:
        if self.status_monitor:
            self.status_monitor.stop()
            self.status_monitor = None
        self.peak_bar.configure(value=0)
        self._set_label(self.meter_label, "Not connected. The meter starts with Mercury.", "gray")
        self.meter_detail.configure(text="")

    def _show_status(self, status: dict) -> None:
        peak = status.get("tx_peak_dbfs")
        has_peak = isinstance(peak, (int, float)) and peak > -120
        self.peak_bar.configure(value=max(0.0, min(40.0, peak + 40.0)) if has_peak else 0)
        snr = status.get("snr")
        parts = [f"TX peak {peak:.1f} dBFS" if has_peak else "TX peak —",
                 f"RX SNR {snr:.1f} dB" if isinstance(snr, (int, float)) and status.get("sync") else "RX SNR —",
                 "audio OK" if status.get("audio_ok") is not False else "AUDIO FAILED",
                 (status.get("direction") or "").upper()]
        if status.get("bitrate"):
            parts.append(f"{status['bitrate']} bps")
        color = "green"
        detail = ""
        if status.get("audio_ok") is False:
            color, detail = "red", f"Mercury reports a sound card failure: {status.get('audio_error')}"
        elif has_peak and peak > mercury_status.PEAK_LIMIT_DBFS:
            color = "orange"
            detail = (f"TX peak above {mercury_status.PEAK_LIMIT_DBFS} dBFS (80 % of full scale). "
                      "This is Mercury's digital level; check the level on the other PC too.")
        self._set_label(self.meter_label, " · ".join(p for p in parts if p), color)
        self.meter_detail.configure(text=detail, fg=COLORS.get(color, COLORS["gray"]))
    def _record_session_end(self, reason: str, code) -> None:
        if not self.session or self.session_reported_end:
            return
        self.session_reported_end = True
        self.session.update(stopped_utc=runtime.utc_now(), stop_reason=reason, exit_code=code)
        try:
            runtime.write_json(Path(self.session["session_dir"]) / "session.json", self.session)
        except OSError:
            pass

    def _stop_mercury(self) -> None:
        if not self.mercury.running():
            return
        if self._test_running() and not messagebox.askyesno(
                "Mercury", "A test is running. Stopping Mercury will make it fail. Stop anyway?"):
            return
        code = self.mercury.stop()
        self._stop_monitor()
        self._record_session_end("stopped_by_operator", code)
        self.start_mercury_button.configure(state="normal")
        self.stop_mercury_button.configure(state="disabled")
        self._set_label(self.mercury_status, "Mercury was stopped.", "gray")
        self._append_log("Mercury stopped by the operator.")

    def _check_ports(self) -> None:
        modem = self.values["modem"].get()
        pid = self.mercury.pid if self.mercury.running() else None
        self._in_background(self._check_ports_worker, modem, pid)

    def _port_verdict(self, modem: str, pid: int | None) -> dict:
        """Who owns 8300/8301, judged for the selected modem (runs in a worker thread)."""
        listeners = listeners_with_pid(netstat_text(), PORTS)
        if modem == "vara":
            verdict = runtime.preflight(listeners, None)
            if pid is not None:
                return dict(verdict, ok=False, needs_confirmation=False,
                            message="Mercury started from this window is running. Press Stop "
                                    "Mercury, then open VARA.")
            owner = process_name(verdict["owner_pids"][0]) if verdict["owner_pids"] else None
            return vara.owner_verdict(verdict, owner)
        verdict = runtime.preflight(listeners, pid)
        if verdict["ok"] and pid is None and verdict["owner_pids"]:
            owner = process_name(verdict["owner_pids"][0])
            if owner and owner.lower() == vara.PROCESS_NAME:
                return dict(verdict, ok=False, needs_confirmation=False,
                            message="Ports 8300 and 8301 belong to VARA. Close VARA, or select "
                                    "VARA HF as the modem.")
        return verdict

    def _check_ports_worker(self, modem: str, pid: int | None) -> None:
        self._call(self._show_port_check, modem, self._port_verdict(modem, pid))

    def _status_label(self, modem: str):
        return self.vara_status if modem == "vara" else self.mercury_status

    def _show_port_check(self, modem: str, verdict: dict) -> None:
        color = "green" if verdict["ok"] and not verdict["needs_confirmation"] else (
            "orange" if verdict["ok"] else "red")
        self._set_label(self._status_label(modem), verdict["message"], color)

    # ------------------------------------------------------------------ VARA
    def _browse_vara(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select VARA.exe", filetypes=[("VARA.exe", "VARA.exe"), ("Programs", "*.exe")])
        if selected:
            self.values["vara_exe"].set(selected)
            self._save_settings()
            self._check_vara()

    def _check_vara(self) -> None:
        self._set_label(self.vara_status, "Checking VARA…", "gray")
        self._in_background(self._check_vara_worker, self.values["vara_exe"].get(),
                            self.values["mycall"].get().strip().upper(),
                            self.mercury.pid if self.mercury.running() else None)

    def _check_vara_worker(self, exe: str, mycall: str, pid: int | None) -> None:
        try:
            info = vara.inspect(exe)
        except (OSError, vara.VaraError, ValueError) as error:
            self._call(self._show_vara, None, [str(error)], [], None)
            return
        errors, warnings = vara.setting_notes(info, mycall)
        self._call(self._show_vara, info, errors, warnings, self._port_verdict("vara", pid))

    def _show_vara(self, info: dict | None, errors: list[str], warnings: list[str],
                   verdict: dict | None) -> None:
        self.vara_info = info
        if info is None:
            self._set_label(self.vara_status, "VARA NOT READY. " + " ".join(errors), "red")
            self.vara_detail.configure(text="")
            return
        arm = logic.vara_arm(info["registered"])
        text = (f"{arm}. VARA.exe SHA-256 {info['exe_sha256'][:16]}…  Devices: "
                f"{info['settings']['input_device']} / {info['settings']['output_device']}. "
                "Registration codes are never read into the results.")
        if verdict:
            text += "  " + verdict["message"]
        ready = not errors and verdict is not None and verdict["ok"]
        self._set_label(self.vara_status, text, "green" if ready and not verdict["needs_confirmation"]
                        else ("orange" if ready else "red"))
        self.vara_detail.configure(text="\n".join(errors + warnings),
                                   fg=COLORS["red"] if errors else COLORS["orange"])
        self._test_changed()

    # ------------------------------------------------------------------ smoke tests
    def _run(self) -> None:
        if self._test_running():
            return
        values = self._form_values()
        errors = logic.validate_form(values)
        if errors:
            messagebox.showwarning("Missing or invalid data", "\n".join(errors))
            return
        self.values["mycall"].set(values["mycall"])
        self.values["peer_call"].set(values["peer_call"])
        self._save_settings()
        if logic.is_calibration(self.test_name.get()):
            self._run_calibration(values)
            return
        self.run_button.configure(state="disabled")
        pid = self.mercury.pid if self.mercury.running() else None
        self._in_background(self._preflight_worker, values, pid)

    def calibration_devices(self, values: dict) -> tuple[str | None, str | None]:
        """The interface connected to the other PC: Mercury's selection, else VARA's."""
        capture, playback = self._selected("capture"), self._selected("playback")
        if capture and playback:
            return capture["name"], playback["name"]
        settings = (self.vara_info or {}).get("settings") or {}
        return settings.get("input_device"), settings.get("output_device")

    def _run_calibration(self, values: dict) -> None:
        if self.mercury.running():
            messagebox.showwarning("Stop Mercury first", "The calibration plays and records on the "
                                   "interface itself. Press Stop Mercury on this PC (and close VARA).")
            return
        busy = runtime.busy_ports(listeners_with_pid(netstat_text(), PORTS))
        if busy:
            messagebox.showwarning("A modem is running", "Ports 8300/8301 are in use (PID "
                                   + ", ".join(str(p) for pids in busy.values() for p in pids)
                                   + "). Close VARA or Mercury, then run the calibration.")
            return
        input_device, output_device = self.calibration_devices(values)
        if not input_device or not output_device:
            messagebox.showwarning("Audio interface unknown", "Press Detect audio devices and choose the "
                                   "interface (Mercury section), or press Check VARA, then run again.")
            return
        (HERE / "smoke_config.toml").write_text(logic.config_text(values), encoding="utf-8")
        self.log.delete("1.0", "end")
        self._append_log(f"=== Bench calibration · {'PC A' if values['role'] == 'A' else 'PC B'} · "
                         f"input '{input_device}', output '{output_device}' ===")
        if values["role"] == "A":
            self._append_log("PC A: start this 5–30 s after PC B shows 'recording'.")
        self._set_summary("Calibration running… Do not close this window.", "gray")
        self._set_busy(True)
        commands = logic.calibration_commands(values["role"], input_device, output_device)
        self.worker = threading.Thread(target=self._run_commands, args=(commands, None), daemon=True)
        self.worker.start()

    def _preflight_worker(self, values: dict, pid: int | None) -> None:
        verdict = self._port_verdict(values["modem"], pid)
        vara_info, vara_errors, vara_warnings = None, [], []
        if values["modem"] == "vara":
            try:
                vara_info = vara.inspect(values["vara_exe"])
                vara_errors, vara_warnings = vara.setting_notes(vara_info, values["mycall"])
            except (OSError, vara.VaraError, ValueError) as error:
                vara_errors = [str(error)]
        self._call(self._after_preflight, values, verdict, vara_info, vara_errors, vara_warnings)

    def _after_preflight(self, values: dict, verdict: dict, vara_info: dict | None = None,
                         vara_errors: list[str] | None = None,
                         vara_warnings: list[str] | None = None) -> None:
        modem = values["modem"]
        name = logic.MODEMS[modem]
        problems = list(vara_errors or [])
        if not verdict["ok"]:
            problems.insert(0, verdict["message"])
        if problems:
            self.run_button.configure(state="normal")
            self._set_label(self._status_label(modem), " ".join(problems), "red")
            messagebox.showwarning(f"{name} is not ready", "\n".join(problems))
            return
        if verdict["needs_confirmation"] and not messagebox.askyesno(
                f"{name}: please confirm", verdict["message"] + "\n\nRun the test anyway?"):
            self.run_button.configure(state="normal")
            return
        session = self.session if (modem == "mercury" and self.session and self.mercury.running()) else None
        if vara_info:
            self.vara_info = vara_info
            vara_info = dict(vara_info, pid=(verdict["owner_pids"] or [None])[0],
                             process_name=verdict.get("owner_name"))
        (HERE / "smoke_config.toml").write_text(logic.config_text(values, session, vara_info),
                                                encoding="utf-8")
        test_name = self.test_name.get()
        sequence = logic.is_sequence(test_name)
        repetition = self.repeat.get() if (sequence or logic.is_h1(test_name)) else 1
        if sequence:
            commands = logic.sequence_commands(values["role"], repetition)
            tag = "Full sequence from step " + str(repetition)
        else:
            commands = logic.mercury_commands(logic.MERCURY_TESTS[test_name], values["role"], repetition)
            tag = logic.test_tag(test_name, repetition)
        self.log.delete("1.0", "end")
        self._append_log(f"=== {tag} · {'SENDER (PC A)' if values['role'] == 'A' else 'RECEIVER (PC B)'}"
                         f" · {name}" + (f" · {logic.vara_arm(vara_info['registered'])}" if vara_info else "")
                         + " ===")
        self._append_log(verdict["message"])
        for note in logic.callsign_notes(values) + list(vara_warnings or []):
            self._append_log("Note: " + note)
        if verdict["all_interfaces"]:
            self._append_log(f"Note: {name} listens on all network interfaces; keep this PC "
                             "off the network during the test.")
        if values["role"] == "A":
            self._append_log("PC A: start this only after PC B shows 'waiting_for_connection'.")
        self._set_summary(f"Running {tag}… Do not close this window.", "gray")
        self._set_busy(True)
        window = None
        if modem == "mercury" and self.status_monitor and session and session.get("status_log"):
            window = {"start": mercury_status.utc_now(), "log": session["status_log"]}
        self.worker = threading.Thread(target=self._run_commands, args=(commands, window), daemon=True)
        self.worker.start()

    def _run_commands(self, commands: list[list[str]], window: dict | None = None) -> None:
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        latest_result: str | None = None
        for command in commands:
            self.event_queue.put(("log", "\n>>> " + " ".join(command)))
            try:
                self.process = subprocess.Popen(
                    process_command(command), cwd=str(HERE), env=environment,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", creationflags=flags,
                )
            except OSError as error:
                self.event_queue.put(("log", f"Could not start script: {error}"))
                self.event_queue.put(("summary", json.dumps({"exception": str(error)})))
                break
            assert self.process.stdout is not None
            for line in self.process.stdout:
                line = line.rstrip("\n")
                self.event_queue.put(("log", line))
                step = logic.sequence_step_from_line(line)
                if step:
                    # A test of the full sequence starts: new level window and progress line.
                    if window:
                        window["start"] = mercury_status.utc_now()
                    self.event_queue.put(("progress", f"Test {step[0]} of {step[1]}: {step[2]} "
                                                      "running… Do not close this window."))
                    continue
                path = logic.result_path_from_line(line)
                if not path:
                    continue
                result_path = HERE / path
                if result_path.name == "result.json":
                    # One test finished: its level summary and result now, not at the end.
                    self._publish_result(result_path, window)
                else:
                    latest_result = path
            exit_code = self.process.wait()
            report_path = HERE / latest_result if latest_result else None
            if report_path and report_path.is_file():
                self.event_queue.put(("summary", report_path.read_text(encoding="utf-8")))
            if exit_code != 0:
                break
        self.event_queue.put(("done", ""))

    def _publish_result(self, result_path: Path, window: dict | None) -> None:
        if not result_path.is_file():
            return
        if window:
            self._write_level_summary(window, result_path.parent)
        self.event_queue.put(("summary", result_path.read_text(encoding="utf-8")))

    def _write_level_summary(self, window: dict, run_dir: Path) -> None:
        """Store Mercury's levels during the test beside its result.json."""
        summary = mercury_status.summarize_window(window["log"], window["start"],
                                                  mercury_status.utc_now())
        try:
            (run_dir / "level_summary.json").write_text(json.dumps(summary, indent=2),
                                                        encoding="utf-8")
        except OSError:
            return
        if summary.get("available"):
            peak = summary["tx_peak_max_dbfs"]
            line = (f"Level during the test: TX peak max "
                    f"{'—' if peak is None else f'{peak:.1f} dBFS'}"
                    f"{' (ABOVE 80 % OF FULL SCALE)' if summary['tx_peak_over_limit'] else ''}, "
                    f"RX SNR {summary['rx_snr_min_db']}–{summary['rx_snr_max_db']} dB, "
                    f"audio {'OK' if summary['audio_ok'] else 'FAILED: ' + ', '.join(summary['audio_errors'])}.")
        else:
            line = "Level during the test: Mercury's status was not available."
        self.event_queue.put(("log", line))

    def _drain_queue(self) -> None:
        try:
            while True:
                event, data = self.event_queue.get_nowait()
                if event == "log":
                    self._append_log(data)
                elif event == "summary":
                    try:
                        text, color = logic.summarize(json.loads(data))
                    except ValueError:
                        text, color = "Could not parse the result file.", "red"
                    self._set_summary(text, color)
                elif event == "progress":
                    self.selected_label.configure(text="Now: " + data)
                elif event == "done":
                    self._set_busy(False)
                    self._test_changed()
                elif event == "call":
                    function, args = data
                    function(*args)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self._append_log("Stopped by user.")

    # ------------------------------------------------------------------ tools
    def _open_results(self) -> None:
        RESULTS.mkdir(exist_ok=True)
        if os.name == "nt":
            os.startfile(str(RESULTS))  # noqa: S606
        else:
            messagebox.showinfo("Results", str(RESULTS))

    def _zip_results(self) -> None:
        if not RESULTS.exists():
            messagebox.showinfo("Results", "There are no results yet.")
            return
        destination = HERE / f"results_{self.values['role'].get()}_{time.strftime('%Y%m%d_%H%M%S')}"
        archive = shutil.make_archive(str(destination), "zip", str(RESULTS))
        messagebox.showinfo("Results ZIP", f"Created:\n{archive}")

    def _cross_check_zip(self) -> None:
        if self._test_running():
            return
        archive = filedialog.askopenfilename(
            title="Results ZIP created on the other PC (Create results ZIP)",
            filetypes=[("Results ZIP", "*.zip")],
        )
        if not archive:
            return
        RESULTS.mkdir(exist_ok=True)
        self.log.delete("1.0", "end")
        self._append_log("Pairing every test of this PC with the other PC's results by test name "
                         "and start time. Pairs of different tests are rejected.")
        self._set_summary("Cross-checking the session…", "gray")
        self._set_busy(True)
        command = ["smoke_cross_check.py", "--pair", str(RESULTS), archive]
        self.worker = threading.Thread(target=self._run_commands, args=([command], None), daemon=True)
        self.worker.start()

    def _cross_check(self) -> None:
        sender = filedialog.askopenfilename(
            title="1/2: sender result.json (PC A)", initialdir=str(RESULTS),
            filetypes=[("result.json", "result.json"), ("JSON", "*.json")],
        )
        if not sender:
            return
        receiver = filedialog.askopenfilename(
            title="2/2: receiver result.json (PC B)", initialdir=str(RESULTS),
            filetypes=[("result.json", "result.json"), ("JSON", "*.json")],
        )
        if not receiver or self._test_running():
            return
        self.log.delete("1.0", "end")
        self._set_busy(True)
        self.worker = threading.Thread(target=self._run_cross_check, args=(sender, receiver), daemon=True)
        self.worker.start()

    def _run_cross_check(self, sender: str, receiver: str) -> None:
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(
            process_command(["smoke_cross_check.py", sender, receiver]), cwd=str(HERE),
            env=environment, capture_output=True, text=True, encoding="utf-8",
            errors="replace", creationflags=flags,
        )
        self.event_queue.put(("log", result.stdout + result.stderr))
        cross_check = Path(receiver).with_name("cross_check.json")
        if cross_check.is_file():
            self.event_queue.put(("summary", cross_check.read_text(encoding="utf-8")))
        self.event_queue.put(("done", ""))

    def _close(self) -> None:
        if self._test_running():
            if not messagebox.askyesno("Test in progress", "Close and stop it?"):
                return
            self._stop()
        if self.mercury.running():
            answer = messagebox.askyesnocancel(
                "Mercury is running",
                "Stop Mercury before closing?\n\nYes: stop Mercury and close.\n"
                "No: leave Mercury running and close.\nCancel: keep this window open.")
            if answer is None:
                return
            if answer:
                code = self.mercury.stop()
                self._record_session_end("stopped_on_close", code)
            else:
                self._record_session_end("left_running_on_close", None)
        if self.status_monitor:
            self.status_monitor.stop()
        self.destroy()


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Python version", "Python 3.11 or newer is required.")
        sys.exit(1)
    ModemSmokeWindow().mainloop()
