# core/tts_engine.py
import subprocess
import hashlib
import time
import logging
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)

class TTSEngine:
    FALLBACK_VOICES = [
        {"name": "ru-RU-SvetlanaNeural", "gender": "female", "locale": "ru-RU"},
        {"name": "ru-RU-DmitryNeural", "gender": "male", "locale": "ru-RU"},
        {"name": "en-US-JennyNeural", "gender": "female", "locale": "en-US"},
        {"name": "en-US-GuyNeural", "gender": "male", "locale": "en-US"},
    ]

    def __init__(self, voice: str = "ru-RU-SvetlanaNeural"):
        self.voice = voice or "ru-RU-SvetlanaNeural"
        self.outputs_dir = Path("outputs")
        self.outputs_dir.mkdir(exist_ok=True)

    def generate(self, text, voice=None, rate="+0%", volume="+0%", pitch="+0Hz", **kwargs) -> str:
        voice = voice or self.voice
        if not voice:
            voice = "ru-RU-SvetlanaNeural"
        file_hash = hashlib.md5(f"{text}{voice}{time.time()}".encode()).hexdigest()[:10]
        output_path = self.outputs_dir / f"tts_{file_hash}.mp3"
        cmd = [
            "edge-tts", "--text", text, "--voice", voice,
            f"--rate={rate}", f"--volume={volume}", f"--pitch={pitch}",
            "--write-media", str(output_path)
        ]
        try:
            logger.debug(f"Running edge-tts: {' '.join(cmd)}")
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            return str(output_path)
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode() if e.stderr else str(e)
            logger.error(f"edge-tts failed: {error_msg}")
            raise RuntimeError(f"TTS generation failed: {error_msg}")
        except Exception as e:
            logger.error(f"Unexpected error during TTS: {e}")
            raise

    # ---------- ИСПРАВЛЕННЫЙ МЕТОД ДЛЯ ПОТОКОВОЙ ГЕНЕРАЦИИ ----------
    def generate_stream(self, text, voice=None, rate="+0%", volume="+0%", pitch="+0Hz") -> subprocess.Popen:
        voice = voice or self.voice or "ru-RU-SvetlanaNeural"
        cmd = [
            "edge-tts", "--text", text, "--voice", voice,
            f"--rate={rate}", f"--volume={volume}", f"--pitch={pitch}",
            "--write-media", "-"
        ]
        logger.info(f"🔊 Streaming TTS: {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,   # оставляем PIPE, но читаем только при ошибке
                stdin=subprocess.DEVNULL
            )
            return proc
        except Exception as e:
            logger.error(f"Failed to start streaming TTS: {e}")
            raise

    def list_voices(self) -> List[dict]:
        try:
            result = subprocess.run(
                ["edge-tts", "--list-voices"],
                capture_output=True, text=True, timeout=10
            )
            voices = []
            for line in result.stdout.split('\n'):
                if 'Name:' in line:
                    import re
                    match = re.search(r'Name:\s*([^\s,]+).*?Gender:\s*(\w+).*?Locale:\s*([^\s,]+)', line)
                    if match:
                        voices.append({
                            "name": match.group(1),
                            "gender": match.group(2).lower(),
                            "locale": match.group(3)
                        })
            if voices:
                return voices
        except Exception as e:
            logger.warning(f"Ошибка получения списка голосов: {e}")
        return self.FALLBACK_VOICES

    def is_ready(self) -> bool:
        return True