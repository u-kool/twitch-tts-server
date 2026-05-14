# core/xtts_engine.py
import os
import json
import time
import hashlib
import logging
import threading
import requests
from pathlib import Path
from typing import Optional, List, Generator

logger = logging.getLogger(__name__)

MODEL_REPO = "coqui/XTTS-v2"
MODEL_VERSION = "v2.0.2"
MODEL_FILES = {
    "config.json": f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_VERSION}/config.json?download=true",
    "model.pth": f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_VERSION}/model.pth?download=true",
    "dvae.pth": f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_VERSION}/dvae.pth?download=true",
    "mel_stats.pth": f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_VERSION}/mel_stats.pth?download=true",
    "speakers_xtts.pth": f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_VERSION}/speakers_xtts.pth?download=true",
    "vocab.json": f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_VERSION}/vocab.json?download=true",
}

LANGS = [
    "ar", "zh-cn", "cs", "nl", "en", "fr", "de", "hu",
    "hi", "it", "ja", "ko", "pl", "pt", "ru", "es", "tr",
]

class XTTSv2Engine:
    def __init__(self, voice: str = "female_01.wav", language: str = "ru",
                 temperature: float = 0.7, repetition_penalty: float = 10.0):
        self.voice = voice
        self.language = language
        self.temperature = temperature
        self.repetition_penalty = repetition_penalty
        self._model = None
        self._device = None
        self._lock = threading.Lock()
        self.model_dir = Path("models") / "xttsv2_2.0.2"
        self.voices_dir = Path("voices")
        self.outputs_dir = Path("outputs")
        self.latents_dir = Path("latents")
        self.outputs_dir.mkdir(exist_ok=True)
        self.voices_dir.mkdir(exist_ok=True)
        self.latents_dir.mkdir(exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)

    @property
    def model(self):
        if self._model is None:
            self._load_model()
        return self._model

    @property
    def device(self):
        if self._device is None:
            self._ensure_torch()
            import torch
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"XTTS device: {self._device}")
        return self._device

    def is_model_downloaded(self) -> bool:
        return all(((self.model_dir / f).exists() and (self.model_dir / f).stat().st_size > 0) for f in MODEL_FILES)

    def download_progress(self) -> dict:
        total = len(MODEL_FILES)
        downloaded = sum(1 for f in MODEL_FILES if (self.model_dir / f).exists())
        sizes = {}
        for f in MODEL_FILES:
            p = self.model_dir / f
            sizes[f] = p.stat().st_size if p.exists() else 0
        return {"total": total, "downloaded": downloaded, "sizes": sizes}

    def download_model(self, progress_callback=None):
        for filename, url in MODEL_FILES.items():
            dest = self.model_dir / filename
            if dest.exists() and dest.stat().st_size > 0:
                continue
            logger.info(f"Downloading {filename}...")
            downloaded = 0
            total = 0
            # Determine existing partial size for resume
            if dest.exists():
                downloaded = dest.stat().st_size
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                    if downloaded > 0:
                        headers["Range"] = f"bytes={downloaded}-"
                    r = requests.get(url, stream=True, timeout=(30, 120), headers=headers)
                    if r.status_code == 416:
                        # Range not satisfiable - file is complete
                        break
                    if downloaded > 0 and r.status_code == 206:
                        total = int(r.headers.get("content-length", 0)) + downloaded
                    else:
                        r.raise_for_status()
                        total = int(r.headers.get("content-length", 0))
                        downloaded = 0
                    mode = "ab" if downloaded > 0 else "wb"
                    with open(dest, mode) as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if progress_callback:
                                    progress_callback(filename, downloaded, total)
                    break
                except Exception as e:
                    logger.warning(f"Download {filename} attempt {attempt+1} failed: {e}")
                    if attempt < max_retries - 1:
                        import time as _time
                        _time.sleep(3 * (attempt + 1))
                    else:
                        logger.error(f"Failed to download {filename} after {max_retries} attempts")
                        raise
        logger.info("Model download complete")

    def _ensure_torch(self):
        missing = []
        try:
            import torch
        except ImportError:
            missing.append("torch")
        try:
            import torchaudio
        except ImportError:
            missing.append("torchaudio")
        try:
            import TTS
        except ImportError:
            missing.append("TTS")
        if missing:
            raise ImportError(
                f"Missing: {', '.join(missing)}. "
                "Install: pip install TTS torch torchaudio\n"
                "Requires Python 3.9-3.11 and CUDA-capable GPU (or CPU, very slow)."
            )
        # Check transformers compatibility
        try:
            import transformers
            from packaging import version
            if version.parse(transformers.__version__) >= version.parse("4.41.0"):
                raise ImportError(
                    f"Transformers {transformers.__version__} is too new for TTS. "
                    "Downgrade: pip install transformers==4.40.2"
                )
        except ImportError:
            pass
        except Exception:
            pass

    def _load_model(self):
        self._ensure_torch()
        with self._lock:
            if self._model is not None:
                return
            if not self.is_model_downloaded():
                raise RuntimeError("Model not downloaded. Call download_model() first.")
            try:
                from TTS.tts.configs.xtts_config import XttsConfig
                from TTS.tts.models.xtts import Xtts
            except ImportError as e:
                raise ImportError(
                    f"Coqui TTS not installed ({e}). Run: pip install TTS torch torchaudio"
                )
            # PyTorch 2.6+ defaults weights_only=True, breaking TTS checkpoint loading
            import torch
            if not hasattr(torch, '_weights_only_patched'):
                _orig_torch_load = torch.load
                def _patched_torch_load(f, map_location=None, **kwargs):
                    kwargs.setdefault('weights_only', False)
                    return _orig_torch_load(f, map_location=map_location, **kwargs)
                torch.load = _patched_torch_load
                torch._weights_only_patched = True
            # torchaudio load/save work natively with cu124 build
            logger.info("Loading XTTSv2 model...")
            config_path = self.model_dir / "config.json"
            config = XttsConfig()
            config.load_json(str(config_path))
            model = Xtts.init_from_config(config)
            model.load_checkpoint(
                config,
                checkpoint_dir=str(self.model_dir),
                vocab_path=str(self.model_dir / "vocab.json"),
                use_deepspeed=False,
            )
            model.to(self.device)
            self._model = model
            logger.info("XTTSv2 model loaded")

    def _get_voice_path(self, voice: str) -> Path:
        p = self.voices_dir / voice
        if not p.exists():
            raise FileNotFoundError(f"Voice file not found: {p}")
        return p

    def _latent_cache_key(self, voice_path: Path) -> str:
        stat = voice_path.stat()
        raw = f"{voice_path.name}{stat.st_size}{stat.st_mtime}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _get_latents(self, voice: str):
        voice_path = self._get_voice_path(voice)
        cache_key = self._latent_cache_key(voice_path)
        cache_path = self.latents_dir / f"{cache_key}.pt"
        if cache_path.exists():
            import torch
            data = torch.load(cache_path, map_location=self.device, weights_only=False)
            logger.info(f"Loaded cached latents for {voice}")
            return data["gpt_cond_latent"], data["speaker_embedding"]
        m = self.model
        gpt_cond_latent, speaker_embedding = m.get_conditioning_latents(
            audio_path=[str(voice_path)],
            gpt_cond_len=m.config.gpt_cond_len,
            max_ref_length=m.config.max_ref_len,
            sound_norm_refs=m.config.sound_norm_refs,
        )
        import torch
        torch.save({"gpt_cond_latent": gpt_cond_latent, "speaker_embedding": speaker_embedding}, cache_path)
        logger.info(f"Cached latents for {voice} -> {cache_path.name}")
        return gpt_cond_latent, speaker_embedding

    def generate(self, text: str, voice: Optional[str] = None,
                 language: Optional[str] = None,
                 temperature: Optional[float] = None,
                 repetition_penalty: Optional[float] = None,
                 **kwargs) -> str:
        import torch
        import torchaudio
        voice = voice or self.voice
        language = language or self.language
        temperature = temperature if temperature is not None else self.temperature
        repetition_penalty = repetition_penalty if repetition_penalty is not None else self.repetition_penalty
        m = self.model
        gpt_cond_latent, speaker_embedding = self._get_latents(voice)
        output = m.inference(
            text=text,
            language=language,
            gpt_cond_latent=gpt_cond_latent,
            speaker_embedding=speaker_embedding,
            temperature=temperature,
            length_penalty=float(m.config.length_penalty),
            repetition_penalty=repetition_penalty,
            top_k=int(m.config.top_k),
            top_p=float(m.config.top_p),
            enable_text_splitting=True,
        )
        file_hash = hashlib.md5(f"{text}{voice}{language}{time.time()}".encode()).hexdigest()[:10]
        output_path = self.outputs_dir / f"tts_{file_hash}.wav"
        torchaudio.save(str(output_path), torch.tensor(output["wav"]).unsqueeze(0), 24000)
        logger.info(f"XTTS generated: {text[:50]}... -> {output_path.name}")
        return str(output_path)

    def generate_stream(self, text: str, voice: Optional[str] = None,
                        language: Optional[str] = None,
                        temperature: Optional[float] = None,
                        repetition_penalty: Optional[float] = None):
        import torch
        voice = voice or self.voice
        language = language or self.language
        temperature = temperature if temperature is not None else self.temperature
        repetition_penalty = repetition_penalty if repetition_penalty is not None else self.repetition_penalty
        m = self.model
        gpt_cond_latent, speaker_embedding = self._get_latents(voice)
        output = m.inference_stream(
            text=text,
            language=language,
            gpt_cond_latent=gpt_cond_latent,
            speaker_embedding=speaker_embedding,
            temperature=temperature,
            length_penalty=float(m.config.length_penalty),
            repetition_penalty=repetition_penalty,
            top_k=int(m.config.top_k),
            top_p=float(m.config.top_p),
            enable_text_splitting=True,
            stream_chunk_size=20,
        )
        for chunk in output:
            wav = chunk["wav"]
            yield wav.tobytes()

    def list_voices(self) -> List[dict]:
        voices = []
        for f in sorted(self.voices_dir.iterdir()):
            if f.suffix.lower() in (".wav", ".mp3", ".ogg", ".flac"):
                voices.append({
                    "name": f.name,
                    "path": str(f),
                    "engine": "xtts",
                })
        return voices

    def list_languages(self) -> List[dict]:
        return [{"code": lang, "name": lang} for lang in LANGS]

    def is_ready(self) -> bool:
        return self._model is not None
