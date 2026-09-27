from __future__ import annotations

import queue
import numpy as np
import sounddevice as sd


class LiveAudioInput:
    def __init__(self, device=None, sample_rate=48000, blocksize=4096):
        self.device = device
        self.sample_rate = int(sample_rate)
        self.blocksize = int(blocksize)
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)
        self.stream = None
        self.last_level_db = -120.0

    @staticmethod
    def input_devices() -> list[tuple[int, str]]:
        out = []
        for idx, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0)) > 0:
                out.append((idx, str(d.get("name", f"Device {idx}"))))
        return out

    def _callback(self, indata, frames, time, status):
        mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
        rms = float(np.sqrt(np.mean(mono * mono) + 1e-12))
        self.last_level_db = 20.0 * np.log10(max(rms, 1e-9))
        try:
            self.queue.put_nowait(mono)
        except queue.Full:
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(mono)
            except queue.Empty:
                pass

    def start(self):
        self.stop()
        self.stream = sd.InputStream(
            device=self.device,
            channels=1,
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            dtype="float32",
            callback=self._callback,
        )
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
