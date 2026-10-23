"""Minimal audio playback and recording for the bench calibration.

Windows: the waveIn/waveOut API of winmm.dll through ctypes (no extra packages, so
it also works inside the packaged executable). It goes through the Windows audio
engine in shared mode, like the modems, so the Windows levels (playback 100,
recording 54 on this bench) apply to the calibration as they apply to the modems.

Tests: HF_ARQ_AUDIO_DIR=<folder> selects a file backend. play() writes the signal
to <folder>/<device>.played.wav; record() returns silence plus every signal that
another device "played" into the folder while it was recording, with the gain and
noise set by HF_ARQ_AUDIO_GAIN_DB and HF_ARQ_AUDIO_NOISE_DBFS.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import random
import struct
import sys
import time
import wave
from array import array
from pathlib import Path

RATE = 48000
CHANNELS = 2
SAMPLE_BYTES = 2
FRAME_BYTES = CHANNELS * SAMPLE_BYTES


class AudioError(RuntimeError):
    pass


# ------------------------------------------------------------------ device names
def match_device(wanted: str, available: list[str]) -> int:
    """Index of the device that matches a Mercury/VARA device name.

    winmm truncates names to 31 characters, so "UCA222-A (USB Audio CODEC )" may
    appear shortened. An exact or truncated match wins; otherwise the part before
    " (" must match exactly one device.
    """
    want = wanted.strip().lower()
    names = [a.strip().lower() for a in available]
    for i, name in enumerate(names):
        if name == want or (len(name) >= 20 and want.startswith(name)) or name.startswith(want[:31]):
            return i
    stem = want.split(" (")[0].strip()
    hits = [i for i, name in enumerate(names) if name.split(" (")[0].strip() == stem]
    if len(hits) == 1:
        return hits[0]
    raise AudioError(f"audio device '{wanted}' not found; available: {available}")


# ------------------------------------------------------------------ winmm backend
if sys.platform == "win32":
    from ctypes import wintypes

    _winmm = ctypes.WinDLL("winmm")

    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                    ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                    ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                    ("cbSize", wintypes.WORD)]

    class WAVEHDR(ctypes.Structure):
        pass

    WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                        ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                        ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                        ("lpNext", ctypes.POINTER(WAVEHDR)), ("reserved", ctypes.c_size_t)]

    class WAVEOUTCAPSW(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("dwFormats", wintypes.DWORD),
                    ("wChannels", wintypes.WORD), ("wReserved1", wintypes.WORD),
                    ("dwSupport", wintypes.DWORD)]

    class WAVEINCAPSW(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("dwFormats", wintypes.DWORD),
                    ("wChannels", wintypes.WORD), ("wReserved1", wintypes.WORD)]

    HWAVE = ctypes.c_void_p
    MMRESULT = wintypes.UINT
    _sig = {
        "waveOutGetNumDevs": ([], wintypes.UINT),
        "waveInGetNumDevs": ([], wintypes.UINT),
        "waveOutGetDevCapsW": ([ctypes.c_size_t, ctypes.POINTER(WAVEOUTCAPSW), wintypes.UINT], MMRESULT),
        "waveInGetDevCapsW": ([ctypes.c_size_t, ctypes.POINTER(WAVEINCAPSW), wintypes.UINT], MMRESULT),
        "waveOutOpen": ([ctypes.POINTER(HWAVE), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
                         ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD], MMRESULT),
        "waveInOpen": ([ctypes.POINTER(HWAVE), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
                        ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD], MMRESULT),
        "waveOutPrepareHeader": ([HWAVE, ctypes.POINTER(WAVEHDR), wintypes.UINT], MMRESULT),
        "waveOutUnprepareHeader": ([HWAVE, ctypes.POINTER(WAVEHDR), wintypes.UINT], MMRESULT),
        "waveOutWrite": ([HWAVE, ctypes.POINTER(WAVEHDR), wintypes.UINT], MMRESULT),
        "waveOutReset": ([HWAVE], MMRESULT),
        "waveOutClose": ([HWAVE], MMRESULT),
        "waveInPrepareHeader": ([HWAVE, ctypes.POINTER(WAVEHDR), wintypes.UINT], MMRESULT),
        "waveInUnprepareHeader": ([HWAVE, ctypes.POINTER(WAVEHDR), wintypes.UINT], MMRESULT),
        "waveInAddBuffer": ([HWAVE, ctypes.POINTER(WAVEHDR), wintypes.UINT], MMRESULT),
        "waveInStart": ([HWAVE], MMRESULT),
        "waveInStop": ([HWAVE], MMRESULT),
        "waveInReset": ([HWAVE], MMRESULT),
        "waveInClose": ([HWAVE], MMRESULT),
    }
    for _name, (_args, _res) in _sig.items():
        _f = getattr(_winmm, _name)
        _f.argtypes, _f.restype = _args, _res

    WHDR_DONE = 0x1
    WAVE_FORMAT_PCM = 1

    def _check(code: int, what: str) -> None:
        if code != 0:
            raise AudioError(f"{what} failed (MMRESULT {code})")

    def _format() -> "WAVEFORMATEX":
        return WAVEFORMATEX(WAVE_FORMAT_PCM, CHANNELS, RATE, RATE * FRAME_BYTES, FRAME_BYTES,
                            8 * SAMPLE_BYTES, 0)

    def output_devices() -> list[str]:
        names = []
        for i in range(_winmm.waveOutGetNumDevs()):
            caps = WAVEOUTCAPSW()
            if _winmm.waveOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
                names.append(caps.szPname)
        return names

    def input_devices() -> list[str]:
        names = []
        for i in range(_winmm.waveInGetNumDevs()):
            caps = WAVEINCAPSW()
            if _winmm.waveInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
                names.append(caps.szPname)
        return names

    def _play_winmm(device: str, pcm: bytes) -> None:
        index = match_device(device, output_devices())
        handle = HWAVE()
        fmt = _format()
        _check(_winmm.waveOutOpen(ctypes.byref(handle), index, ctypes.byref(fmt), 0, 0, 0), "waveOutOpen")
        buffer = ctypes.create_string_buffer(pcm, len(pcm))
        header = WAVEHDR(ctypes.cast(buffer, ctypes.c_void_p), len(pcm), 0, 0, 0, 0, None, 0)
        try:
            _check(_winmm.waveOutPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header)),
                   "waveOutPrepareHeader")
            _check(_winmm.waveOutWrite(handle, ctypes.byref(header), ctypes.sizeof(header)), "waveOutWrite")
            deadline = time.monotonic() + len(pcm) / (RATE * FRAME_BYTES) + 10
            while not header.dwFlags & WHDR_DONE:
                if time.monotonic() > deadline:
                    raise AudioError("playback did not finish")
                time.sleep(0.05)
        finally:
            _winmm.waveOutReset(handle)
            _winmm.waveOutUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
            _winmm.waveOutClose(handle)

    def _record_winmm(device: str, seconds: float) -> bytes:
        index = match_device(device, input_devices())
        nbytes = int(seconds * RATE) * FRAME_BYTES
        handle = HWAVE()
        fmt = _format()
        _check(_winmm.waveInOpen(ctypes.byref(handle), index, ctypes.byref(fmt), 0, 0, 0), "waveInOpen")
        buffer = ctypes.create_string_buffer(nbytes)
        header = WAVEHDR(ctypes.cast(buffer, ctypes.c_void_p), nbytes, 0, 0, 0, 0, None, 0)
        try:
            _check(_winmm.waveInPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header)),
                   "waveInPrepareHeader")
            _check(_winmm.waveInAddBuffer(handle, ctypes.byref(header), ctypes.sizeof(header)),
                   "waveInAddBuffer")
            _check(_winmm.waveInStart(handle), "waveInStart")
            deadline = time.monotonic() + seconds + 10
            while not header.dwFlags & WHDR_DONE:
                if time.monotonic() > deadline:
                    raise AudioError("recording did not finish")
                time.sleep(0.05)
            return buffer.raw[:header.dwBytesRecorded]
        finally:
            _winmm.waveInStop(handle)
            _winmm.waveInReset(handle)
            _winmm.waveInUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
            _winmm.waveInClose(handle)
else:
    def output_devices() -> list[str]:
        return []

    def input_devices() -> list[str]:
        return []


# ------------------------------------------------------------------ file backend (tests)
def _file_dir() -> Path | None:
    value = os.environ.get("HF_ARQ_AUDIO_DIR")
    return Path(value) if value else None


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_BYTES)
        w.setframerate(RATE)
        w.writeframes(pcm)


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (CHANNELS, SAMPLE_BYTES, RATE):
            raise AudioError(f"{path}: expected 48 kHz, 16 bit, stereo")
        return w.readframes(w.getnframes())


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _play_file(folder: Path, device: str, pcm: bytes) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    start = time.time()
    tmp = folder / f"{_safe(device)}.tmp"
    write_wav(tmp, pcm)
    (folder / f"{_safe(device)}.played.json").write_text(json.dumps({"start": start}), encoding="utf-8")
    os.replace(tmp, folder / f"{_safe(device)}.played.wav")
    time.sleep(len(pcm) / (RATE * FRAME_BYTES))


def _record_file(folder: Path, device: str, seconds: float) -> bytes:
    start = time.time()
    time.sleep(seconds)
    frames = int(seconds * RATE)
    gain = 10 ** (float(os.environ.get("HF_ARQ_AUDIO_GAIN_DB", "0")) / 20)
    noise = 32768 * 10 ** (float(os.environ.get("HF_ARQ_AUDIO_NOISE_DBFS", "-80")) / 20)
    rng = random.Random(1)
    mix = [rng.gauss(0.0, noise) for _ in range(frames * CHANNELS)]
    for played in folder.glob("*.played.wav"):
        if played.name.startswith(_safe(device) + "."):
            continue
        meta = json.loads(played.with_suffix(".json").read_text(encoding="utf-8"))
        offset = int((meta["start"] - start) * RATE) * CHANNELS
        samples = array("h", read_wav(played))
        for i, value in enumerate(samples):
            j = offset + i
            if 0 <= j < len(mix):
                mix[j] += gain * value
    return struct.pack(f"<{len(mix)}h", *(max(-32768, min(32767, int(round(v)))) for v in mix))


# ------------------------------------------------------------------ public API
def play(device: str, pcm: bytes) -> None:
    folder = _file_dir()
    if folder:
        return _play_file(folder, device, pcm)
    if sys.platform != "win32":
        raise AudioError("audio playback is available on Windows only")
    return _play_winmm(device, pcm)


def record(device: str, seconds: float) -> bytes:
    folder = _file_dir()
    if folder:
        return _record_file(folder, device, seconds)
    if sys.platform != "win32":
        raise AudioError("audio recording is available on Windows only")
    return _record_winmm(device, seconds)


def dbfs(value: float) -> float | None:
    return None if value <= 0 else 20 * math.log10(value / 32768)
