import logging
import queue
import threading
import time
from pathlib import Path

from .xtts_engine import XTTSv2Engine


logger = logging.getLogger(__name__)


def normalize_tts_param(value: str, suffix: str = "%") -> str:
    if not value:
        return f"+0{suffix}"
    value = value.strip()
    if value.startswith("+") or value.startswith("-"):
        return value
    return f"+{value}"


class TTSRunner:
    def __init__(self, engine, get_config, log_callback, event_callback,
                 max_queue_size: int = 200, concurrency_limit: int = 1):
        self.engine = engine
        self.get_config = get_config
        self.log_callback = log_callback
        self.event_callback = event_callback
        self.task_queue = queue.Queue(maxsize=max_queue_size)
        self.semaphore = threading.BoundedSemaphore(value=concurrency_limit)
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.task_queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

    def queue_size(self) -> int:
        return self.task_queue.qsize()

    def enqueue(self, text: str, voice: str = None, rate: str = None,
                volume: str = None, pitch: str = None, **kwargs) -> bool:
        cfg = self.get_config()
        if not cfg.get("tts_enabled", True):
            return False
        try:
            self.task_queue.put_nowait({
                "text": text,
                "voice": voice,
                "rate": rate,
                "volume": volume,
                "pitch": pitch,
                "kwargs": kwargs,
            })
            return True
        except queue.Full:
            logger.warning("TTS queue is full; dropping message")
            return False

    def _process(self, task):
        text = task["text"]
        voice = task["voice"]
        rate = task["rate"]
        volume = task["volume"]
        pitch = task["pitch"]
        kwargs = task["kwargs"]
        cfg = self.get_config()

        with self.semaphore:
            try:
                if isinstance(self.engine, XTTSv2Engine):
                    out = self.engine.generate(
                        text=text,
                        voice=voice or cfg.get("xtts_voice", "female_01.wav"),
                        language=kwargs.get("language") or cfg.get("xtts_language", "ru"),
                        temperature=float(kwargs.get("temperature") or cfg.get("xtts_temperature", 0.75)),
                        repetition_penalty=float(kwargs.get("repetition_penalty") or cfg.get("xtts_repetition_penalty", 10.0)),
                    )
                else:
                    voice = voice or cfg["voice"]
                    rate = rate or normalize_tts_param(cfg.get("rate", "+0%"), "%")
                    volume = volume or normalize_tts_param(cfg.get("volume", "+0%"), "%")
                    pitch = pitch or normalize_tts_param(cfg.get("pitch", "+0Hz"), "Hz")
                    out = self.engine.generate(text=text, voice=voice, rate=rate, volume=volume, pitch=pitch)
                fname = Path(out).name
                logger.info(f"TTS: {text[:50]}... -> {fname}")
                self.log_callback("system", f"Озвучено: {text[:60]}...")
                self.event_callback({"event": "new_audio", "filename": fname, "timestamp": time.time()})
            except Exception as e:
                logger.error(f"TTS error: {e}")
                self.log_callback("error", f"TTS Error: {e}")

    def _worker(self):
        logger.info("TTS worker started")
        while not self._stop.is_set():
            try:
                task = self.task_queue.get(timeout=1)
            except queue.Empty:
                continue
            if task is None:
                break
            self._process(task)
        logger.info("TTS worker stopped")
