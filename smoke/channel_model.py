"""Two-path Watterson HF channel with band-limited Gaussian noise (protocol v5.3).

Pure numpy and deterministic: the same seeds give the same output sample for sample,
whatever the block size. See docs/phase1_channel_design.md for the definitions.

Model, per direction:
    xa(n)  = analytic signal of the input (complex band-pass, 200-3400 Hz)
    y(n)   = Re{ g1(n) xa(n) + g2(n) xa(n - D) }          two Rayleigh paths, E|g1|^2 = E|g2|^2 = 1/2
    out(n) = G_out ( y(n) + w(n) )                          w: Gaussian, 300-3300 Hz, power N
with g1, g2 complex Gaussian processes whose Doppler power spectrum is Gaussian with
2 sigma = the frequency spread, D = the differential delay, N = P_ref / 10^(SNR/10),
P_ref = power of the reference sine (the modems' common peak), and G_out = -12 dB.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

FS = 48000
BLOCK = 1024
FIR_TAPS = 1025               # band-pass filters; group delay (FIR_TAPS - 1) / 2 samples
FADING_RATE = 50.0            # Hz, rate of the path-gain processes before interpolation
OUTPUT_GAIN_DB = -12.0
SEED_ROOT = 20261023
REFERENCE_PEAK_DBFS = -6.0    # the modems' common transmit peak on this bench


@dataclass(frozen=True)
class Condition:
    code: int
    name: str
    delay_ms: float
    spread_hz: float          # 2 sigma of the Gaussian Doppler power spectrum
    snr_db: float             # in 3 kHz, referred to the reference sine


CONDITIONS = {
    "good": Condition(1, "Good", 0.5, 0.1, 10.0),
    "moderate": Condition(2, "Moderate", 1.0, 0.5, 3.0),
    "poor": Condition(3, "Poor", 2.0, 1.0, -3.0),
}
DIRECTIONS = {"A->B": 0, "B->A": 1}


# ------------------------------------------------------------------ filters
def lowpass(cutoff_hz: float, taps: int = FIR_TAPS) -> np.ndarray:
    """Blackman-windowed sinc low-pass with unit DC gain."""
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff_hz / FS * n) * np.blackman(taps)
    return h / h.sum()


def analytic_bandpass() -> np.ndarray:
    """Complex filter passing +200..+3400 Hz and rejecting negative frequencies."""
    n = np.arange(FIR_TAPS) - (FIR_TAPS - 1) / 2
    return lowpass(1600.0) * np.exp(2j * np.pi * 1800.0 * n / FS)


def noise_bandpass() -> np.ndarray:
    """Real filter passing 300..3300 Hz (3 kHz), the SNR reference band."""
    n = np.arange(FIR_TAPS) - (FIR_TAPS - 1) / 2
    return 2 * np.real(lowpass(1500.0) * np.exp(2j * np.pi * 1800.0 * n / FS))


class StreamFIR:
    """Overlap-save FIR filter that keeps its state between blocks of any length."""

    def __init__(self, h: np.ndarray):
        self.h = np.asarray(h)
        self.tail = np.zeros(len(h) - 1, dtype=np.result_type(self.h, np.float64))
        self.complex = np.iscomplexobj(self.h)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x)
        joined = np.concatenate([self.tail, x])
        nfft = 1 << int(np.ceil(np.log2(len(joined) + len(self.h))))
        if self.complex or np.iscomplexobj(joined):
            y = np.fft.ifft(np.fft.fft(joined, nfft) * np.fft.fft(self.h, nfft))
        else:
            y = np.fft.irfft(np.fft.rfft(joined, nfft) * np.fft.rfft(self.h, nfft), nfft)
        out = y[len(self.h) - 1:len(joined)]
        self.tail = joined[len(joined) - (len(self.h) - 1):] if len(self.h) > 1 else self.tail
        return out


# ------------------------------------------------------------------ fading
def doppler_fir(spread_hz: float) -> np.ndarray:
    """Real Gaussian FIR at FADING_RATE that turns unit complex white noise into a
    process of variance 1/2 with a Gaussian power spectrum of standard deviation
    spread/2. |H(f)|^2 has std sigma_f when h(t) is Gaussian with
    sigma_t = 1 / (2 pi sqrt(2) sigma_f)."""
    sigma_f = spread_hz / 2
    sigma_t = 1 / (2 * math.pi * math.sqrt(2) * sigma_f)
    half = int(math.ceil(4 * sigma_t * FADING_RATE))
    t = np.arange(-half, half + 1) / FADING_RATE
    h = np.exp(-t ** 2 / (2 * sigma_t ** 2))
    return h * math.sqrt(0.5 / np.sum(h ** 2))


class FadingTap:
    """Complex path gain g(n) at the audio rate, from a seeded generator."""

    CHUNK = 512  # low-rate samples generated at a time

    def __init__(self, spread_hz: float, rng: np.random.Generator):
        self.h = doppler_fir(spread_hz)
        self.rng = rng
        warm = self._white(len(self.h) - 1)
        self.fir = StreamFIR(self.h.astype(complex))
        self.fir.tail = warm
        self.low = np.zeros(0, dtype=complex)   # generated low-rate samples not yet consumed
        self.low_index = 0                      # index of self.low[0] on the low-rate grid
        self.n = 0                              # next audio sample index

    def _white(self, count: int) -> np.ndarray:
        z = self.rng.standard_normal((count, 2))
        return (z[:, 0] + 1j * z[:, 1]) / math.sqrt(2)

    def _ensure(self, last_index: int) -> None:
        while self.low_index + len(self.low) <= last_index:
            self.low = np.concatenate([self.low, self.fir.process(self._white(self.CHUNK))])

    def gains(self, count: int) -> np.ndarray:
        t = (self.n + np.arange(count)) * (FADING_RATE / FS)
        i0 = np.floor(t).astype(np.int64)
        self._ensure(int(i0[-1]) + 1)
        frac = t - i0
        a = self.low[i0 - self.low_index]
        b = self.low[i0 + 1 - self.low_index]
        self.n += count
        drop = int(i0[-1]) - self.low_index
        if drop > 0:
            self.low = self.low[drop:]
            self.low_index += drop
        return a + (b - a) * frac


# ------------------------------------------------------------------ one direction
def reference_power(peak_dbfs: float = REFERENCE_PEAK_DBFS) -> float:
    """Power of a sine with the given peak, full scale = 1.0."""
    amplitude = 10 ** (peak_dbfs / 20)
    return amplitude ** 2 / 2


def seeds(condition: str, direction: str, attempt: int) -> list[np.random.SeedSequence]:
    root = np.random.SeedSequence([SEED_ROOT, CONDITIONS[condition].code, DIRECTIONS[direction], attempt])
    return root.spawn(3)


class ChannelPath:
    """One direction of the simulator. mode: 'transparent' or a key of CONDITIONS."""

    def __init__(self, mode: str, direction: str, p_ref: float, attempt: int = 1,
                 output_gain_db: float = OUTPUT_GAIN_DB):
        self.mode, self.direction, self.p_ref = mode, direction, p_ref
        self.gain = 10 ** (output_gain_db / 20)
        self.sig = StreamFIR(analytic_bandpass())
        self.noise_fir = StreamFIR(noise_bandpass())
        self.noise_sigma = 0.0
        self.attempt = attempt
        if mode != "transparent":
            cond = CONDITIONS[mode]
            self.delay = int(round(cond.delay_ms * 1e-3 * FS))
            self.noise_power = p_ref / 10 ** (cond.snr_db / 10)
            self.noise_sigma = math.sqrt(self.noise_power / float(np.sum(noise_bandpass() ** 2)))
        self.reset(attempt)

    def reset(self, attempt: int) -> None:
        """Restart fading and noise from attempt k's seeds (filters keep their state)."""
        self.attempt = attempt
        if self.mode == "transparent":
            return
        cond = CONDITIONS[self.mode]
        s1, s2, s3 = seeds(self.mode, self.direction, attempt)
        self.tap1 = FadingTap(cond.spread_hz, np.random.default_rng(s1))
        self.tap2 = FadingTap(cond.spread_hz, np.random.default_rng(s2))
        self.noise_rng = np.random.default_rng(s3)
        self.history = np.zeros(self.delay, dtype=complex)

    def process(self, x: np.ndarray, static_gains: tuple[complex, complex] | None = None,
                noise: bool = True) -> np.ndarray:
        """x: real block, full scale 1.0. static_gains/noise are for validation only."""
        x = np.asarray(x, dtype=np.float64)
        if self.mode == "transparent":
            return self.gain * x
        xa = 2 * self.sig.process(x)
        joined = np.concatenate([self.history, xa])
        delayed = joined[:len(xa)]
        self.history = joined[len(xa):] if self.delay else self.history
        if static_gains is None:
            g1, g2 = self.tap1.gains(len(x)), self.tap2.gains(len(x))
        else:
            g1, g2 = static_gains
            self.tap1.gains(len(x)), self.tap2.gains(len(x))   # keep the streams aligned
        y = np.real(g1 * xa + g2 * delayed)
        w = self.noise_fir.process(self.noise_rng.standard_normal(len(x)) * self.noise_sigma)
        return self.gain * (y + (w if noise else 0.0))
