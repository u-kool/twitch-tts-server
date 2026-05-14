import copy
import json
import math
import threading
from pathlib import Path


def deep_merge(base, overrides):
    for key, value in overrides.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def migrate_config(config: dict) -> dict:
    for user, value in config.get("user_voice_map", {}).items():
        if isinstance(value, str):
            config["user_voice_map"][user] = {
                "voice": value,
                "rate": config.get("rate", "+0%"),
                "volume": config.get("volume", "+0%"),
                "pitch": config.get("pitch", "+0Hz"),
            }

    for event_config in config.get("events", {}).values():
        if not isinstance(event_config, dict):
            continue
        event_config.setdefault("rate", config.get("rate", "+0%"))
        event_config.setdefault("volume", config.get("volume", "+0%"))
        event_config.setdefault("pitch", config.get("pitch", "+0Hz"))
        if event_config.get("reward_voice_map") is not None:
            for reward, value in event_config["reward_voice_map"].items():
                if isinstance(value, str):
                    event_config["reward_voice_map"][reward] = {
                        "voice": value,
                        "rate": event_config.get("rate", "+0%"),
                        "volume": event_config.get("volume", "+0%"),
                        "pitch": event_config.get("pitch", "+0Hz"),
                    }
        event_config.pop("enable_unmapped_rewards", None)
        event_config.pop("default_voice", None)
    return config


def _validate_number(data: dict, key: str, minimum: int = 0) -> tuple[bool, str]:
    if key not in data:
        return True, ""
    value = data[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return False, f"{key} must be a finite number"
    if value < minimum:
        return False, f"{key} must be >= {minimum}"
    data[key] = int(value)
    return True, ""


def validate_config_update(data: dict, current_config: dict) -> tuple[bool, str]:
    for key, minimum in {
        "event_cooldown": 0,
        "user_cooldown": 0,
        "min_length": 1,
        "max_length": 1,
    }.items():
        ok, error = _validate_number(data, key, minimum)
        if not ok:
            return False, error
    min_length = data.get("min_length", current_config.get("min_length", 3))
    max_length = data.get("max_length", current_config.get("max_length", 200))
    if min_length > max_length:
        return False, "min_length must be <= max_length"
    return True, ""


class ConfigStore:
    def __init__(self, path: str | Path, default_config: dict):
        self.path = Path(path)
        self.default_config = copy.deepcopy(default_config)
        self._config = copy.deepcopy(default_config)
        self._lock = threading.RLock()

    @property
    def lock(self):
        return self._lock

    def load(self) -> dict:
        with self._lock:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                merged = copy.deepcopy(self.default_config)
                deep_merge(merged, loaded)
                self._config = migrate_config(merged)
            else:
                self._config = copy.deepcopy(self.default_config)
            return self._config

    def save(self, config: dict | None = None):
        with self._lock:
            if config is not None:
                self._config = copy.deepcopy(config)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._config)

    def snapshot_shallow(self) -> dict:
        with self._lock:
            return self._config

    def update(self, data: dict) -> dict:
        with self._lock:
            update_data = copy.deepcopy(data)
            valid, error = validate_config_update(update_data, self._config)
            if not valid:
                raise ValueError(error)
            for key, value in update_data.items():
                if key in self.default_config:
                    self._config[key] = value
            self.save()
            return copy.deepcopy(self._config)

    def get(self, key: str, default=None):
        with self._lock:
            return self._config.get(key, default)

    def __getitem__(self, key):
        with self._lock:
            return self._config[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._config[key] = value

    def __contains__(self, key):
        with self._lock:
            return key in self._config
