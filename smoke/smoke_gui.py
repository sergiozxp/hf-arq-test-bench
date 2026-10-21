"""HF-ARQ Modem Smoke GUI.

This alpha release runs the Mercury 1.9.19 smoke profile only.  It neither
starts Mercury nor changes its settings; it launches the same Python scripts
that are available from a command prompt and presents their result files.
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

import mercury_smoke_client  # noqa: F401  # included by PyInstaller
import smoke_cross_check  # noqa: F401  # included by PyInstaller

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


class ModemSmokeWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HF-ARQ Modem Smoke — Mercury")
        self.geometry("960x735")
        self.event_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.process: subprocess.Popen | None = None
        self.worker: threading.Thread | None = None
        self.values = {
            key: tk.StringVar()
            for key in (
                "role", "mycall", "peer_call", "bw", "mercury_zip",
                "mercury_exe", "mercury_ini", "mercury_log",
            )
        }
        self.values["role"].set("A")
        self.values["bw"].set("BW500")
        self._load_settings()
        self._build()
        self.after(100, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._close)

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

        files = (
            ("mercury_zip", "Mercury ZIP (record SHA-256):"),
            ("mercury_exe", "mercury.exe:"),
            ("mercury_ini", "Mercury configuration (.ini):"),
            ("mercury_log", "Mercury log:"),
        )
        for row, (key, label) in enumerate(files, start=3):
            ttk.Label(station, text=label).grid(row=row, column=0, columnspan=2, sticky="w", **pad)
            ttk.Entry(station, textvariable=self.values[key], width=60).grid(
                row=row, column=2, columnspan=2, sticky="we"
            )
            ttk.Button(station, text="Browse…", command=lambda item=key: self._browse(item)).grid(
                row=row, column=4, **pad
            )

        test = ttk.LabelFrame(self, text="2. Mercury 1.9.19 smoke test")
        test.pack(fill="x", **pad)
        ttk.Label(test, text="Test:").grid(row=0, column=0, sticky="w", **pad)
        self.test_name = tk.StringVar(value=next(iter(logic.MERCURY_TESTS)))
        ttk.Combobox(
            test, textvariable=self.test_name, values=list(logic.MERCURY_TESTS),
            width=62, state="readonly"
        ).grid(row=0, column=1, columnspan=3, sticky="w")
        ttk.Label(test, text="Repeat (H1 only):").grid(row=1, column=0, sticky="w", **pad)
        self.repeat = tk.IntVar(value=1)
        ttk.Spinbox(test, from_=1, to=3, textvariable=self.repeat, width=4).grid(
            row=1, column=1, sticky="w"
        )
        self.run_button = tk.Button(
            test, text="RUN", font=("Segoe UI", 13, "bold"), bg="#1f5fbf",
            fg="white", command=self._run, padx=24, pady=6
        )
        self.run_button.grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        self.stop_button = ttk.Button(test, text="Stop", command=self._stop, state="disabled")
        self.stop_button.grid(row=2, column=2, sticky="w", **pad)

        output = ttk.LabelFrame(self, text="3. Result")
        output.pack(fill="both", expand=True, **pad)
        self.summary = tk.Label(
            output, text="Not run.", font=("Segoe UI", 12, "bold"), fg=COLORS["gray"],
            wraplength=875, justify="left", anchor="w"
        )
        self.summary.pack(fill="x", padx=8, pady=6)
        frame = ttk.Frame(output)
        frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.log = tk.Text(frame, height=13, wrap="word", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        tools = ttk.LabelFrame(self, text="Tools")
        tools.pack(fill="x", **pad)
        ttk.Button(tools, text="Open results folder", command=self._open_results).grid(
            row=0, column=0, padx=4, pady=6
        )
        ttk.Button(tools, text="Create results ZIP", command=self._zip_results).grid(
            row=0, column=1, padx=4, pady=6
        )
        ttk.Button(tools, text="Cross-check A and B results…", command=self._cross_check).grid(
            row=0, column=2, padx=4, pady=6
        )
        ttk.Label(tools, text=f"Working directory: {HERE}", foreground="#555").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=8
        )

    def _browse(self, key: str) -> None:
        selected = filedialog.askopenfilename(title="Select file")
        if selected:
            self.values[key].set(selected)

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

    def _set_busy(self, busy: bool) -> None:
        self.run_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _run(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        values = self._form_values()
        errors = logic.validate_form(values)
        if errors:
            messagebox.showwarning("Missing or invalid data", "\n".join(errors))
            return
        self.values["mycall"].set(values["mycall"])
        self.values["peer_call"].set(values["peer_call"])
        self._save_settings()
        (HERE / "smoke_config.toml").write_text(logic.config_text(values), encoding="utf-8")
        preset = logic.MERCURY_TESTS[self.test_name.get()]
        commands = logic.mercury_commands(preset, values["role"], self.repeat.get())
        self.log.delete("1.0", "end")
        self._set_summary("Running… Do not close this window.", "gray")
        if preset["tag"] == "E0-2_V3":
            self._append_log(logic.V3_INSTRUCTION)
        self._set_busy(True)
        self.worker = threading.Thread(target=self._run_commands, args=(commands,), daemon=True)
        self.worker.start()

    def _run_commands(self, commands: list[list[str]]) -> None:
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
                path = logic.result_path_from_line(line)
                if path:
                    latest_result = path
            exit_code = self.process.wait()
            result_path = HERE / latest_result if latest_result else None
            if result_path and result_path.is_file():
                self.event_queue.put(("summary", result_path.read_text(encoding="utf-8")))
            if exit_code != 0:
                break
        self.event_queue.put(("done", ""))

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
                elif event == "done":
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self._append_log("Stopped by user.")

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
        if not receiver or (self.worker and self.worker.is_alive()):
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
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Test in progress", "Close and stop it?"):
                return
            self._stop()
        self.destroy()


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Python version", "Python 3.11 or newer is required.")
        sys.exit(1)
    ModemSmokeWindow().mainloop()
