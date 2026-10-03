"""Lock-Free SPSC Audio Ring Buffer for J.A.R.V.I.S. — MARK VIII.

Workstream 2:
  1. Pre-allocated shared-memory circular buffer (multiprocessing.shared_memory / numpy buffer).
  2. Single-Producer Single-Consumer (SPSC) layout with atomic read/write indices and underrun tracking.
  3. Zero locks, zero SQLite writes, zero LLM calls on the real-time audio thread.
"""
from __future__ import annotations

import logging
import multiprocessing.shared_memory as shm
import os
import struct
import threading
import time
from typing import Any, Dict, Optional, Tuple, Union

import numpy as np

log = logging.getLogger("jarvis.rt_ring_buffer")


class SPSCAudioRingBuffer:
    """Lock-Free Single-Producer Single-Consumer circular audio buffer."""

    def __init__(
        self,
        capacity_samples: int = 48000 * 2,  # 2 seconds at 48kHz
        sample_rate: int = 48000,
        channels: int = 1,
        dtype: np.dtype = np.float32,
        shared_memory_name: Optional[str] = None,
    ):
        self.capacity_samples = capacity_samples
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = np.dtype(dtype)
        self.itemsize = self.dtype.itemsize

        self.bytes_capacity = self.capacity_samples * self.channels * self.itemsize
        self._shm: Optional[shm.SharedMemory] = None

        # Pre-allocate buffer
        if shared_memory_name:
            try:
                self._shm = shm.SharedMemory(name=shared_memory_name, create=True, size=self.bytes_capacity)
            except FileExistsError:
                self._shm = shm.SharedMemory(name=shared_memory_name, create=False)
            self._buffer = np.ndarray((self.capacity_samples, self.channels), dtype=self.dtype, buffer=self._shm.buf)
        else:
            self._buffer = np.zeros((self.capacity_samples, self.channels), dtype=self.dtype)

        # Atomic index tracking
        self._read_idx = 0
        self._write_idx = 0
        self._underruns = 0
        self._overruns = 0
        self._total_samples_written = 0
        self._total_samples_read = 0

    @property
    def capacity(self) -> int:
        return self.capacity_samples

    def available_read(self) -> int:
        """Calculate number of samples currently available to read without locking."""
        w = self._write_idx
        r = self._read_idx
        if w >= r:
            return w - r
        return self.capacity_samples - (r - w)

    def available_write(self) -> int:
        """Calculate remaining write space in samples."""
        return (self.capacity_samples - 1) - self.available_read()

    def write(self, data: Union[np.ndarray, bytes, list]) -> int:
        """Write audio samples to ring buffer. Lock-free real-time producer path."""
        if isinstance(data, bytes):
            arr = np.frombuffer(data, dtype=self.dtype)
        else:
            arr = np.asarray(data, dtype=self.dtype)

        if arr.ndim == 1 and self.channels == 1:
            arr = arr.reshape(-1, 1)

        num_samples = arr.shape[0]
        avail = self.available_write()

        if num_samples > avail:
            self._overruns += 1
            # Drop overflow or write what fits
            write_count = avail
            if write_count == 0:
                return 0
            arr = arr[:write_count]
        else:
            write_count = num_samples

        w = self._write_idx
        first_chunk = min(write_count, self.capacity_samples - w)
        second_chunk = write_count - first_chunk

        self._buffer[w : w + first_chunk] = arr[:first_chunk]
        if second_chunk > 0:
            self._buffer[0:second_chunk] = arr[first_chunk:]

        self._write_idx = (w + write_count) % self.capacity_samples
        self._total_samples_written += write_count
        return write_count

    def read(self, num_samples: int) -> np.ndarray:
        """Read audio samples from ring buffer. Lock-free real-time consumer path."""
        avail = self.available_read()
        if avail < num_samples:
            self._underruns += 1
            read_count = avail
        else:
            read_count = num_samples

        if read_count == 0:
            return np.zeros((0, self.channels), dtype=self.dtype)

        r = self._read_idx
        first_chunk = min(read_count, self.capacity_samples - r)
        second_chunk = read_count - first_chunk

        out = np.empty((read_count, self.channels), dtype=self.dtype)
        out[:first_chunk] = self._buffer[r : r + first_chunk]
        if second_chunk > 0:
            out[first_chunk:] = self._buffer[0:second_chunk]

        self._read_idx = (r + read_count) % self.capacity_samples
        self._total_samples_read += read_count
        return out

    def get_underruns(self) -> int:
        return self._underruns

    def get_overruns(self) -> int:
        return self._overruns

    def clear(self) -> None:
        self._read_idx = 0
        self._write_idx = 0

    def close(self) -> None:
        if self._shm:
            try:
                self._shm.close()
                self._shm.unlink()
            except Exception:
                pass


_global_audio_ring: Optional[SPSCAudioRingBuffer] = None


def get_audio_ring_buffer() -> SPSCAudioRingBuffer:
    global _global_audio_ring
    if _global_audio_ring is None:
        _global_audio_ring = SPSCAudioRingBuffer()
    return _global_audio_ring
