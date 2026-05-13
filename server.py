#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎙️ Twitch TTS Server v7.9.2 – Унифицированная настройка наград, поддержка __silent__
"""
import os
import sys
import json
import time
import asyncio
import logging
import threading
import queue
import requests
import http.server
import urllib.parse
import webbrowser
import subprocess
import hashlib
import re
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, Response
from werkzeug.utils import secure_filename

# ========== НАСТРОЙКИ Twitch API ==========
CLIENT_ID = "fsiif72enf4wf6jg4omgxtif5aj0y9"
CLIENT_SECRET = "eqi3d46kce2mt1z3hdz7eflw27lcjd"
REDIRECT_URI = "http://localhost:3000/redirect/"
OAUTH_PORT = 3000
# =========================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('server.log', encoding='utf-8', mode='a')
    ]
)
logger = logging.getLogger(__name__)

logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

from core.tts_engine import TTSEngine
from core.twitch_api_client import TwitchApiClient
from core.twitch_eventsub_api import TwitchEventSubClient
from irc_bot import TwitchIRCBot

CONFIG_FILE = Path("config.json")
OUTPUTS_DIR = Path("outputs")
VOICES_DIR = Path("voices")

for d in [OUTPUTS_DIR, VOICES_DIR]:
    d.mkdir(exist_ok=True)

# Расширенная конфигурация по умолчанию
DEFAULT_CONFIG = {
    "twitch_token": "",
    "twitch_refresh_token": "",
    "twitch_channel": "",
    "twitch_user_id": "",
    "twitch_login": "",
    "filter_mods": True,
    "filter_broadcaster": True,
    "min_length": 3,
    "max_length": 200,
    "user_cooldown": 10,
    "event_cooldown": 5,
    "voice": "ru-RU-SvetlanaNeural",
    "rate": "+0%",
    "volume": "+0%",
    "pitch": "+0Hz",
    "host": "127.0.0.1",
    "port": 5000,
    "save_audio": True,
    "tts_enabled": True,
    "read_all_messages": True,
    "read_only_answered": False,
    "role_filters": {
        "highlighted": True,
        "subscription": False,
        "vip": False,
        "moderator": True
    },
    "filter_links": True,
    "filter_emotes": False,
    "use_keywords": False,
    "keywords": ["!tts"],
    "strip_keywords_from_tts": True,
    "ignore_chars": "@",
    "blacklist_users": ["Nightbot", "Moobot", "StreamElements", "ynot_lordius", "u_psik", "u_kool"],
    "whitelist_users": [],
    "user_voice_map": {},
    "text_replacements": [],
    "events": {
        "follow": {"enabled": True, "voice": "ru-RU-SvetlanaNeural", "format": "{UserName} отслеживает {Service} канал", "rate": "+0%", "volume": "+0%", "pitch": "+0Hz"},
        "subscription": {"enabled": True, "voice": "ru-RU-SvetlanaNeural", "format": "{UserName} подписался на {Service} канал (уровень {Tier})", "rate": "+0%", "volume": "+0%", "pitch": "+0Hz"},
        "subscription_gift": {"enabled": True, "voice": "ru-RU-SvetlanaNeural", "format": "{UserName} подарил {Total} подписок", "rate": "+0%", "volume": "+0%", "pitch": "+0Hz"},
        "cheer": {"enabled": True, "voice": "ru-RU-SvetlanaNeural", "format": "{UserName} отправил {Bits} битсов", "rate": "+0%", "volume": "+0%", "pitch": "+0Hz"},
        "raid": {"enabled": False, "voice": "ru-RU-SvetlanaNeural", "format": "{UserName} начал рейд на {Service} канал и привел {Viewers} зрителей", "min_viewers": 0, "rate": "+0%", "volume": "+0%", "pitch": "+0Hz"},
        "reward": {
            "enabled": True,
            "voice": "ru-RU-SvetlanaNeural",
            "format_no_msg": "{UserName} использовал награду {RewardName}",
            "format_with_msg": "{UserName} использовал награду {RewardName} и сказал {Message}",
            "reward_voice_map": {},
            "rate": "+0%",
            "volume": "+0%",
            "pitch": "+0Hz"
        }
    }
}

def _normalize_tts_param(value: str, suffix: str = '%') -> str:
    if not value:
        return f"+0{suffix}"
    value = value.strip()
    if value.startswith('+') or value.startswith('-'):
        return value
    return f"+{value}"

def deep_merge(base, overrides):
    for key, value in overrides.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            merged = DEFAULT_CONFIG.copy()
            deep_merge(merged, config)
            # Миграция: преобразовать строковые user_voice_map в объекты при необходимости
            for user, val in merged.get("user_voice_map", {}).items():
                if isinstance(val, str):
                    merged["user_voice_map"][user] = {"voice": val, "rate": merged.get("rate", "+0%"), "volume": merged.get("volume", "+0%"), "pitch": merged.get("pitch", "+0Hz")}
            # Миграция для событий: добавить rate/volume/pitch если их нет
            for ev in merged.get("events", {}).values():
                if isinstance(ev, dict):
                    ev.setdefault("rate", merged.get("rate", "+0%"))
                    ev.setdefault("volume", merged.get("volume", "+0%"))
                    ev.setdefault("pitch", merged.get("pitch", "+0Hz"))
                    if ev.get("reward_voice_map") is not None:
                        for reward, cfg in ev["reward_voice_map"].items():
                            if isinstance(cfg, str):
                                ev["reward_voice_map"][reward] = {"voice": cfg, "rate": ev.get("rate", "+0%"), "volume": ev.get("volume", "+0%"), "pitch": ev.get("pitch", "+0Hz")}
                    # Удаляем устаревшие ключи
                    ev.pop("enable_unmapped_rewards", None)
                    ev.pop("default_voice", None)
            return merged
        except Exception as e:
            logger.warning(f"⚠️ Config error: {e}")
    return DEFAULT_CONFIG.copy()

def save_config(config: dict) -> bool:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"❌ Save config error: {e}")
        return False

config = load_config()
cached_emotes = {}
emotes_last_fetch = 0
EMOTES_CACHE_TTL = 600
tts_engine = TTSEngine(voice=config["voice"])
message_queue = queue.Queue(maxsize=200)
twitch_bot: TwitchIRCBot = None
twitch_running = False
event_sub_client: TwitchEventSubClient = None
event_sub_thread: threading.Thread = None

last_tts_time = {}
last_event_tts_time = 0

sse_queue = queue.Queue()
sse_clients = []
sse_lock = threading.Lock()

app = Flask(__name__,
            template_folder="templates",
            static_folder="static",
            static_url_path="/static"
           )

emoteMap = {}

# === OAuth Server ===
class OAuthHandler(http.server.BaseHTTPRequestHandler):
    code = None
    error = None
    event = threading.Event()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if "code" in query:
            OAuthHandler.code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<html><body><h2>✅ Успешная авторизация!</h2><p>Можно закрыть окно.</p></body></html>".encode())
            OAuthHandler.event.set()
        elif "error" in query:
            OAuthHandler.error = query["error"][0]
            self.send_response(400)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<html><body><h2>❌ Ошибка: {OAuthHandler.error}</h2></body></html>".encode())
            OAuthHandler.event.set()
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def start_oauth_server():
    server = http.server.HTTPServer(("localhost", OAUTH_PORT), OAuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread

def get_auth_url():
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join([
            "chat:read", "chat:edit",
            "channel:read:subscriptions", "channel:read:redemptions",
            "bits:read", "channel:read:hype_train",
            "moderator:read:followers",
            "channel:manage:redemptions",
        ]),
        "force_verify": "true"
    }
    return "https://id.twitch.tv/oauth2/authorize?" + urllib.parse.urlencode(params)

def exchange_code_for_token(code):
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    }
    r = requests.post("https://id.twitch.tv/oauth2/token", data=data)
    r.raise_for_status()
    token_data = r.json()
    return token_data["access_token"], token_data.get("refresh_token")

def get_user_from_token(access_token):
    headers = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {access_token}"
    }
    r = requests.get("https://api.twitch.tv/helix/users", headers=headers)
    r.raise_for_status()
    users = r.json()["data"]
    if not users:
        raise Exception("Не удалось получить данные пользователя")
    return users[0]["id"], users[0]["login"]

def perform_full_oauth():
    OAuthHandler.code = None
    OAuthHandler.error = None
    OAuthHandler.event.clear()
    logger.info("🌐 Запуск OAuth-сервера...")
    server, thread = start_oauth_server()
    auth_url = get_auth_url()
    logger.info("🚀 Открытие браузера для авторизации...")
    webbrowser.open(auth_url)
    if not OAuthHandler.event.wait(timeout=120):
        logger.error("❌ Таймаут авторизации")
        server.shutdown()
        return None, None, None, None
    server.shutdown()
    if OAuthHandler.error:
        logger.error(f"❌ Ошибка авторизации: {OAuthHandler.error}")
        return None, None, None, None
    code = OAuthHandler.code
    try:
        access_token, refresh_token = exchange_code_for_token(code)
        user_id, login = get_user_from_token(access_token)
        logger.info(f"✅ Авторизация успешна: {login} (ID: {user_id})")
        return access_token, user_id, login, refresh_token
    except Exception as e:
        logger.error(f"❌ Ошибка получения токена: {e}")
        return None, None, None, None

def log_to_queue(msg_type: str, text: str, user: str = None):
    try:
        message_queue.put_nowait({
            "type": msg_type,
            "user": user,
            "text": text,
            "timestamp": time.time()
        })
    except:
        pass

def broadcast_sse(message: dict):
    with sse_lock:
        for client_queue in sse_clients:
            try:
                client_queue.put_nowait(message)
            except queue.Full:
                pass

def tts_wrapper(text: str, voice: str = None, rate: str = None, volume: str = None, pitch: str = None):
    if not config.get("tts_enabled", True):
        return False
    voice = voice or config["voice"]
    rate = rate or _normalize_tts_param(config.get("rate", "+0%"), '%')
    volume = volume or _normalize_tts_param(config.get("volume", "+0%"), '%')
    pitch = pitch or _normalize_tts_param(config.get("pitch", "+0Hz"), 'Hz')
    try:
        file_hash = hashlib.md5(f"{text}{voice}{time.time()}".encode()).hexdigest()[:10]
        output_path = OUTPUTS_DIR / f"tts_{file_hash}.mp3"
        cmd = [
            "edge-tts", "--text", text, "--voice", voice,
            "--rate", rate, "--volume", volume, "--pitch", pitch,
            "--write-media", str(output_path)
        ]
        logger.info(f"🔊 TTS command: {' '.join(cmd)}")
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        logger.info(f"🔊 TTS: {text[:50]}...")
        log_to_queue("system", f"Озвучено: {text[:60]}...")
        broadcast_sse({"event": "new_audio", "filename": f"tts_{file_hash}.mp3", "timestamp": time.time()})
        return True
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else str(e)
        logger.error(f"❌ TTS error: {error_msg}")
        log_to_queue("error", f"TTS Error: {error_msg}")
        return False
    except Exception as e:
        logger.error(f"❌ TTS error: {e}")
        log_to_queue("error", f"TTS Error: {e}")
        return False

def should_tts_message(event: dict) -> (bool, str, dict):
    if not config.get("tts_enabled", True):
        return False, "", {}
    text = event.get("text", "").strip()
    user = event.get("user", "")
    if not text or not user:
        return False, "", {}

    skip_role_check = False

    whitelist = config.get("whitelist_users", [])
    if user in whitelist:
        skip_role_check = True
    else:
        if user in config.get("blacklist_users", []):
            return False, "", {}
        is_broadcaster = event.get("is_broadcaster", False)
        if is_broadcaster:
            if config.get("filter_broadcaster", True):
                return False, "", {}
            else:
                skip_role_check = True

    min_len = config.get("min_length", 3)
    max_len = config.get("max_length", 200)
    if len(text) < min_len or len(text) > max_len:
        return False, "", {}
    now = time.time()
    cooldown = config.get("user_cooldown", 10)
    if user in last_tts_time and (now - last_tts_time[user]) < cooldown:
        return False, "", {}
    last_tts_time[user] = now

    if config.get("filter_links", True):
        text = re.sub(r'https?://\S+|www\.\S+', '', text).strip()
    global emoteMap
    if config.get("filter_emotes", False) and emoteMap:
        words = text.split()
        new_words = []
        for w in words:
            clean_w = w.strip()
            if clean_w not in emoteMap:
                new_words.append(w)
        text = ' '.join(new_words)
    ignore_chars = config.get("ignore_chars", "")
    if ignore_chars:
        for ch in ignore_chars:
            text = text.replace(ch, '')
    replacements = config.get("text_replacements", [])
    for rep in replacements:
        old = rep.get("from", "")
        new = rep.get("to", "")
        if old:
            text = text.replace(old, new)
    if config.get("use_keywords", False):
        keywords = config.get("keywords", [])
        found = any(kw in text for kw in keywords)
        if not found:
            return False, "", {}
        if config.get("strip_keywords_from_tts", True):
            for kw in keywords:
                text = text.replace(kw, '').strip()
    if not text:
        return False, "", {}

    if not skip_role_check and not config.get("read_all_messages", True):
        role_filters = config.get("role_filters", {})
        is_sub = event.get("is_subscriber", False)
        is_vip = event.get("is_vip", False)
        is_mod = event.get("is_moderator", False)
        is_highlighted = event.get("is_highlighted", False)

        allowed_by_role = False
        if role_filters.get("subscription") and is_sub:
            allowed_by_role = True
        if role_filters.get("vip") and is_vip:
            allowed_by_role = True
        if role_filters.get("moderator") and is_mod:
            allowed_by_role = True
        if role_filters.get("highlighted") and is_highlighted:
            allowed_by_role = True

        if config.get("read_only_answered", False) and not event.get("is_reply", False):
            return False, "", {}

        if not allowed_by_role:
            return False, "", {}

    user_voice_cfg = config.get("user_voice_map", {}).get(user, config.get("voice"))
    if isinstance(user_voice_cfg, dict):
        voice = user_voice_cfg.get("voice", config["voice"])
        rate = _normalize_tts_param(user_voice_cfg.get("rate", config.get("rate", "+0%")), '%')
        volume = _normalize_tts_param(user_voice_cfg.get("volume", config.get("volume", "+0%")), '%')
        pitch = _normalize_tts_param(user_voice_cfg.get("pitch", config.get("pitch", "+0Hz")), 'Hz')
    else:
        voice = user_voice_cfg if isinstance(user_voice_cfg, str) else config["voice"]
        rate = _normalize_tts_param(config.get("rate", "+0%"), '%')
        volume = _normalize_tts_param(config.get("volume", "+0%"), '%')
        pitch = _normalize_tts_param(config.get("pitch", "+0Hz"), 'Hz')
    return True, text, {"voice": voice, "rate": rate, "volume": volume, "pitch": pitch}

def process_event(event_data: dict):
    """Обработка событий (follow, sub, reward, и т.д.)"""
    event_type = event_data.get("type")
    if not event_type:
        return
    
    logger.info(f"🐞 process_event: type={event_type}, data={event_data}")
    
    # Проверка глобального кулдауна
    global last_event_tts_time
    cooldown = config.get("event_cooldown", 5)
    now = time.time()
    if now - last_event_tts_time < cooldown:
        logger.info(f"⏸️ Событие {event_type} пропущено (кулдаун {cooldown}с)")
        return
    last_event_tts_time = now
    
    events_cfg = config.get("events", {})
    ev_cfg = events_cfg.get(event_type, {})
    
    # Проверка включено ли событие
    if not ev_cfg.get("enabled", True):
        logger.info(f"⏸️ Событие {event_type} отключено в настройках")
        return
    
    # Для raid - проверка минимального количества зрителей
    if event_type == "raid":
        if event_data.get("viewers", 0) < ev_cfg.get("min_viewers", 0):
            logger.info(f"⏸️ Рейд отклонён: зрителей {event_data.get('viewers', 0)} < {ev_cfg.get('min_viewers', 0)}")
            return

    # Формирование текста для озвучивания
    template = ""
    if event_type == "reward":
        if event_data.get("message"):
            template = ev_cfg.get("format_with_msg", "{UserName} использовал награду {RewardName} и сказал {Message}")
        else:
            template = ev_cfg.get("format_no_msg", "{UserName} использовал награду {RewardName}")
    else:
        template = ev_cfg.get("format", "")
    
    if not template:
        logger.warning(f"⚠️ Нет шаблона форматирования для события {event_type}")
        return

    # Подстановка переменных
    text = template
    text = text.replace("{UserName}", event_data.get("user", ""))
    text = text.replace("{Service}", "Twitch")
    
    if event_type == "subscription":
        text = text.replace("{Tier}", event_data.get("tier", ""))
    elif event_type == "subscription_gift":
        text = text.replace("{Total}", str(event_data.get("total", 0)))
    elif event_type == "cheer":
        text = text.replace("{Bits}", str(event_data.get("bits", 0)))
    elif event_type == "raid":
        text = text.replace("{Viewers}", str(event_data.get("viewers", 0)))
    elif event_type == "reward":
        reward_name = event_data.get("reward_name", "")
        text = text.replace("{RewardName}", reward_name)
        text = text.replace("{Message}", event_data.get("message", ""))

    if not text or not text.strip():
        logger.warning(f"⚠️ Текст для озвучивания пуст после подстановки: event_type={event_type}")
        return

    # Выбор голоса и параметров TTS
    if event_type == "reward":
        reward_name = event_data.get("reward_name", "")
        reward_map = ev_cfg.get("reward_voice_map", {})
        voice_cfg = reward_map.get(reward_name)
        
        if voice_cfg is None:
            # Используем основной голос события (выбранный в блоке reward)
            voice = ev_cfg.get("voice", config["voice"])
            rate = _normalize_tts_param(ev_cfg.get("rate", config.get("rate", "+0%")), '%')
            volume = _normalize_tts_param(ev_cfg.get("volume", config.get("volume", "+0%")), '%')
            pitch = _normalize_tts_param(ev_cfg.get("pitch", config.get("pitch", "+0Hz")), 'Hz')
        else:
            if isinstance(voice_cfg, str):
                if voice_cfg == "__silent__":
                    logger.info(f"🔇 Награда '{reward_name}' отключена в настройках")
                    return
                voice = voice_cfg
                rate = _normalize_tts_param(ev_cfg.get("rate", config.get("rate", "+0%")), '%')
                volume = _normalize_tts_param(ev_cfg.get("volume", config.get("volume", "+0%")), '%')
                pitch = _normalize_tts_param(ev_cfg.get("pitch", config.get("pitch", "+0Hz")), 'Hz')
            else:  # dict
                if voice_cfg.get("voice") == "__silent__":
                    logger.info(f"🔇 Награда '{reward_name}' отключена в настройках")
                    return
                voice = voice_cfg.get("voice", ev_cfg.get("voice", config["voice"]))
                rate = _normalize_tts_param(voice_cfg.get("rate", ev_cfg.get("rate", config.get("rate", "+0%"))), '%')
                volume = _normalize_tts_param(voice_cfg.get("volume", ev_cfg.get("volume", config.get("volume", "+0%"))), '%')
                pitch = _normalize_tts_param(voice_cfg.get("pitch", ev_cfg.get("pitch", config.get("pitch", "+0Hz"))), 'Hz')
    else:
        voice_cfg = ev_cfg.get("voice", config["voice"])
        if isinstance(voice_cfg, dict):
            voice = voice_cfg.get("voice", config["voice"])
            rate = _normalize_tts_param(voice_cfg.get("rate", ev_cfg.get("rate", config.get("rate", "+0%"))), '%')
            volume = _normalize_tts_param(voice_cfg.get("volume", ev_cfg.get("volume", config.get("volume", "+0%"))), '%')
            pitch = _normalize_tts_param(voice_cfg.get("pitch", ev_cfg.get("pitch", config.get("pitch", "+0Hz"))), 'Hz')
        else:
            voice = voice_cfg
            rate = _normalize_tts_param(ev_cfg.get("rate", config.get("rate", "+0%")), '%')
            volume = _normalize_tts_param(ev_cfg.get("volume", config.get("volume", "+0%")), '%')
            pitch = _normalize_tts_param(ev_cfg.get("pitch", config.get("pitch", "+0Hz")), 'Hz')

    logger.info(f"🔊 Обработка события {event_type}: '{text}' (voice={voice}, rate={rate}, volume={volume}, pitch={pitch})")
    
    # Отправка в TTS
    if config.get("save_audio", True):
        tts_wrapper(text, voice=voice, rate=rate, volume=volume, pitch=pitch)
    else:
        broadcast_sse({
            "event": "play",
            "text": text,
            "voice": voice,
            "rate": rate,
            "volume": volume,
            "pitch": pitch
        })
    
    log_to_queue("event", text, event_data.get("user"))

def handle_message(event: dict):
    """Обработка входящих сообщений от IRC и EventSub"""
    # Если есть признак, что это событие от EventSub – обрабатываем как process_event
    if event.get("_source") == "eventsub":
        process_event(event)
        return
    
    event_type = event.get("type")
    if event_type == "chat":
        text = event.get("text", "").strip()
        user = event.get("user", "Аноним")
        log_to_queue("chat", text, user)
        allowed, processed_text, tts_params = should_tts_message(event)
        if not allowed:
            return
        if config.get("save_audio", True):
            tts_wrapper(
                processed_text,
                voice=tts_params.get("voice"),
                rate=tts_params.get("rate"),
                volume=tts_params.get("volume"),
                pitch=tts_params.get("pitch")
            )
        else:
            broadcast_sse({
                "event": "play",
                "text": processed_text,
                "voice": tts_params.get("voice"),
                "rate": tts_params.get("rate"),
                "volume": tts_params.get("volume"),
                "pitch": tts_params.get("pitch")
            })
    elif event_type == "event":
        # Старый формат (для обратной совместимости)
        process_event(event.get("event_data", event))

def start_event_sub(token, refresh_token, user_id):
    global event_sub_client, event_sub_thread
    if event_sub_client is not None:
        logger.warning("EventSub client already running")
        return

    def eventsub_callback(msg_type, data):
        if msg_type == "event":
            # Добавляем флаг, чтобы handle_message понял, что это событие
            data["_source"] = "eventsub"
            handle_message(data)

    event_sub_client = TwitchEventSubClient(
        CLIENT_ID, CLIENT_SECRET, token, refresh_token, user_id, eventsub_callback
    )

    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(event_sub_client.start())
        except Exception as e:
            logger.error(f"❌ EventSub loop error: {e}")
        finally:
            loop.close()

    event_sub_thread = threading.Thread(target=run_loop, daemon=True)
    event_sub_thread.start()
    logger.info("📡 EventSub thread started")

def stop_event_sub():
    global event_sub_client, event_sub_thread
    if event_sub_client:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(event_sub_client.stop())
            loop.close()
        except Exception as e:
            logger.warning(f"Error stopping EventSub: {e}")
    event_sub_client = None
    event_sub_thread = None

def auto_start_twitch():
    global twitch_bot, twitch_running, config
    if twitch_running:
        logger.info("Twitch уже запущен")
        return

    token = config.get("twitch_token", "").strip()
    channel = config.get("twitch_channel", "").strip()
    login = config.get("twitch_login", "").strip()

    if not token or not channel or not login:
        logger.info("🔐 Нет сохранённой авторизации. Запускаем OAuth...")
        token, user_id, login, refresh_token = perform_full_oauth()
        if not token:
            logger.error("❌ Автоматическая авторизация не удалась. Бот не запущен.")
            return
        channel = f"#{login}"
        config["twitch_token"] = token
        config["twitch_refresh_token"] = refresh_token
        config["twitch_channel"] = channel
        config["twitch_user_id"] = user_id
        config["twitch_login"] = login
        save_config(config)
    else:
        try:
            headers = {
                "Client-ID": CLIENT_ID,
                "Authorization": f"Bearer {token}"
            }
            r = requests.get("https://api.twitch.tv/helix/users", headers=headers, timeout=10)
            if r.status_code != 200:
                logger.info("🔄 Токен недействителен. Повторная авторизация...")
                token, user_id, login, refresh_token = perform_full_oauth()
                if not token:
                    logger.error("❌ Повторная авторизация не удалась. Бот не запущен.")
                    return
                channel = f"#{login}"
                config["twitch_token"] = token
                config["twitch_refresh_token"] = refresh_token
                config["twitch_channel"] = channel
                config["twitch_user_id"] = user_id
                config["twitch_login"] = login
                save_config(config)
        except Exception as e:
            logger.warning(f"Ошибка проверки токена: {e}")

    start_event_sub(token, config.get("twitch_refresh_token", ""), config["twitch_user_id"])

    try:
        nick = channel.lstrip("#")
        twitch_bot = TwitchIRCBot(
            token=token,
            nick=nick,
            channel=channel,
            tts_callback=handle_message
        )
        twitch_bot.start()
        if twitch_bot.wait_connected(timeout=15):
            twitch_running = True
            logger.info(f"📺 Автоматический запуск бота для {channel} успешен")
            log_to_queue("system", f"✅ Бот автоматически подключён к {channel}")
        else:
            logger.error(f"❌ Не удалось подключиться к {channel} (таймаут)")
            log_to_queue("error", f"Не удалось подключиться к {channel}. Проверьте токен и имя канала.")
            twitch_bot.stop()
            twitch_bot = None
    except Exception as e:
        logger.error(f"❌ Ошибка автоматического запуска: {e}")
        log_to_queue("error", str(e))

# ========== МАРШРУТЫ FLASK ==========
@app.route("/")
def index():
    return render_template("index.html", config=config)

@app.route("/api/status")
def api_status():
    return jsonify({
        "tts_ready": tts_engine.is_ready(),
        "twitch_running": twitch_running,
        "channel": config.get("twitch_channel", ""),
        "login": config.get("twitch_login", ""),
        "queue_size": message_queue.qsize(),
        "has_token": bool(config.get("twitch_token"))
    })

@app.route("/api/auth/status")
def auth_status():
    return jsonify({
        "has_token": bool(config.get("twitch_token")),
        "login": config.get("twitch_login", ""),
        "channel": config.get("twitch_channel", "")
    })

@app.route("/api/config", methods=["GET"])
def get_config():
    safe_keys = [
        "voice", "rate", "volume", "pitch", "event_cooldown", "min_length", "max_length",
        "user_cooldown", "filter_broadcaster", "save_audio", "tts_enabled", "read_all_messages",
        "read_only_answered", "role_filters", "filter_links", "filter_emotes", "use_keywords",
        "keywords", "strip_keywords_from_tts", "ignore_chars", "blacklist_users", "whitelist_users",
        "user_voice_map", "text_replacements", "events"
    ]
    result = {k: config.get(k) for k in safe_keys if k in config}
    return jsonify(result)

@app.route("/api/config", methods=["POST"])
def api_config():
    global config
    data = request.json or {}
    for key in data:
        if key in DEFAULT_CONFIG:
            config[key] = data[key]

    # Нормализация user_voice_map
    if "user_voice_map" in data:
        new_map = {}
        for user, val in data["user_voice_map"].items():
            if isinstance(val, dict):
                new_map[user] = val
            elif isinstance(val, str):
                new_map[user] = {"voice": val, "rate": config.get("rate", "+0%"), "volume": config.get("volume", "+0%"), "pitch": config.get("pitch", "+0Hz")}
            else:
                new_map[user] = {"voice": config["voice"], "rate": config.get("rate", "+0%"), "volume": config.get("volume", "+0%"), "pitch": config.get("pitch", "+0Hz")}
        config["user_voice_map"] = new_map

    # Нормализация reward_voice_map в событиях
    if "events" in data:
        for ev_name, ev_cfg in data["events"].items():
            if ev_name == "reward" and "reward_voice_map" in ev_cfg:
                reward_map = ev_cfg["reward_voice_map"]
                new_reward_map = {}
                for reward, val in reward_map.items():
                    if isinstance(val, dict):
                        new_reward_map[reward] = val
                    elif isinstance(val, str):
                        new_reward_map[reward] = {"voice": val, "rate": ev_cfg.get("rate", config.get("rate", "+0%")), "volume": ev_cfg.get("volume", config.get("volume", "+0%")), "pitch": ev_cfg.get("pitch", config.get("pitch", "+0Hz"))}
                    else:
                        new_reward_map[reward] = {"voice": config["voice"], "rate": config.get("rate", "+0%"), "volume": config.get("volume", "+0%"), "pitch": config.get("pitch", "+0Hz")}
                ev_cfg["reward_voice_map"] = new_reward_map
            # Удаляем устаревшие ключи
            ev_cfg.pop("enable_unmapped_rewards", None)
            ev_cfg.pop("default_voice", None)

    if save_config(config):
        tts_engine.voice = config["voice"]
        logger.info("⚙️ TTS config saved")
        return jsonify({"status": "saved"})
    return jsonify({"error": "Save failed"}), 500

@app.route("/api/logs")
def api_logs():
    logs = []
    while not message_queue.empty() and len(logs) < 50:
        try:
            logs.append(message_queue.get_nowait())
        except:
            break
    return jsonify(logs)

@app.route("/api/emotes")
def api_emotes():
    global cached_emotes, emotes_last_fetch, emoteMap
    now = time.time()
    if cached_emotes and (now - emotes_last_fetch) < EMOTES_CACHE_TTL:
        return jsonify(cached_emotes)

    token = config.get("twitch_token", "").strip()
    user_id = config.get("twitch_user_id", "").strip()
    login = config.get("twitch_login", "").strip()
    emotes = {}

    if not token or not user_id:
        cached_emotes = emotes
        emotes_last_fetch = now
        emoteMap = emotes
        return jsonify(emotes)

    headers = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {token}"
    }
    session = requests.Session()
    session.headers.update({"User-Agent": "TwitchTTS/1.0"})

    def fetch_with_retry(url, headers=None, max_retries=1, timeout=8):
        for attempt in range(max_retries):
            try:
                r = session.get(url, headers=headers, timeout=timeout) if headers else session.get(url, timeout=timeout)
                return r
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                else:
                    raise
            except requests.exceptions.RequestException:
                raise

    try:
        # Twitch global emotes
        try:
            r = fetch_with_retry("https://api.twitch.tv/helix/chat/emotes/global", headers=headers)
            if r.status_code == 200:
                for e in r.json().get("data", []):
                    emotes[e["name"]] = f"https://static-cdn.jtvnw.net/emoticons/v2/{e['id']}/default/dark/1.0"
        except Exception as e:
            logger.warning(f"Twitch global emotes: {e}")

        # Twitch channel emotes
        try:
            r = fetch_with_retry(f"https://api.twitch.tv/helix/chat/emotes?broadcaster_id={user_id}", headers=headers)
            if r.status_code == 200:
                for e in r.json().get("data", []):
                    emotes[e["name"]] = f"https://static-cdn.jtvnw.net/emoticons/v2/{e['id']}/default/dark/1.0"
        except Exception as e:
            logger.warning(f"Twitch channel emotes: {e}")

        # BTTV global
        try:
            r = fetch_with_retry("https://api.betterttv.net/3/cached/emotes/global")
            if r.status_code == 200:
                for e in r.json():
                    emotes[e["code"]] = f"https://cdn.betterttv.net/emote/{e['id']}/1x"
        except Exception as e:
            logger.warning(f"BTTV global: {e}")

        # BTTV channel
        if login:
            try:
                r = fetch_with_retry(f"https://api.betterttv.net/3/cached/users/twitch/{user_id}")
                if r.status_code == 200:
                    bttv_data = r.json()
                    for e in bttv_data.get("channelEmotes", []):
                        emotes[e["code"]] = f"https://cdn.betterttv.net/emote/{e['id']}/1x"
                    for e in bttv_data.get("sharedEmotes", []):
                        emotes[e["code"]] = f"https://cdn.betterttv.net/emote/{e['id']}/1x"
            except Exception as e:
                logger.warning(f"BTTV channel: {e}")

        # 7TV global
        try:
            r = fetch_with_retry("https://7tv.io/v3/emote-sets/global", timeout=15, max_retries=2)
            if r.status_code == 200:
                data = r.json()
                for e in data.get("emotes", []):
                    name = e["name"]
                    host = e.get("host", {})
                    files = host.get("files", [])
                    if files:
                        url = next((f"https:{host['url']}/{f['name']}" for f in files if f.get("name", "").endswith("1x.webp")), None)
                        if not url:
                            url = f"https:{host['url']}/{files[0]['name']}"
                        emotes[name] = url
        except Exception as e:
            logger.warning(f"7TV global: {e}")

        # 7TV channel
        if login:
            try:
                r = fetch_with_retry(f"https://7tv.io/v3/users/twitch/{user_id}", timeout=15, max_retries=2)
                if r.status_code == 200:
                    user_data = r.json()
                    for e in user_data.get("emote_set", {}).get("emotes", []):
                        name = e["name"]
                        host = e.get("host", {})
                        files = host.get("files", [])
                        if files:
                            url = next((f"https:{host['url']}/{f['name']}" for f in files if f.get("name", "").endswith("1x.webp")), None)
                            if not url:
                                url = f"https:{host['url']}/{files[0]['name']}"
                            emotes[name] = url
            except Exception as e:
                logger.warning(f"7TV channel: {e}")

        # FFZ global
        try:
            r = fetch_with_retry("https://api.frankerfacez.com/v1/emotes", timeout=15, max_retries=2)
            if r.status_code == 200:
                data = r.json()
                for set_id, set_data in data.get("sets", {}).items():
                    for e in set_data.get("emoticons", []):
                        name = e.get("name")
                        urls = e.get("urls")
                        if name and urls and "1" in urls:
                            emotes[name] = urls["1"]
        except Exception as e:
            logger.warning(f"FFZ global: {e}")

        # FFZ channel
        if login:
            try:
                r = fetch_with_retry(f"https://api.frankerfacez.com/v1/room/{login}", timeout=15, max_retries=2)
                if r.status_code == 200:
                    data = r.json()
                    for set_id, set_data in data.get("sets", {}).items():
                        for e in set_data.get("emoticons", []):
                            name = e.get("name")
                            urls = e.get("urls")
                            if name and urls and "1" in urls:
                                emotes[name] = urls["1"]
            except Exception as e:
                logger.warning(f"FFZ channel: {e}")

    except Exception as e:
        logger.error(f"Unexpected error in emotes loading: {e}")

    cached_emotes = emotes
    emotes_last_fetch = now
    emoteMap = emotes
    logger.info(f"Эмоутов загружено: {len(emotes)} (кэш обновлён)")
    return jsonify(emotes)

@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "Empty text"}), 400
    try:
        output_path = tts_engine.generate(
            text=text,
            voice=data.get("voice", config["voice"]),
            rate=data.get("rate", config["rate"]),
            volume=data.get("volume", config["volume"]),
            pitch=data.get("pitch", config["pitch"])
        )
        return jsonify({"success": True, "output": Path(output_path).name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/latest")
def api_latest():
    file_name = request.args.get("file")
    if file_name:
        target = OUTPUTS_DIR / secure_filename(file_name)
        if target.exists():
            return send_file(target, mimetype="audio/mpeg")
        return jsonify({"error": "File not found"}), 404
    files = list(OUTPUTS_DIR.glob("*.mp3"))
    if not files:
        return jsonify({"error": "No audio"}), 404
    latest = max(files, key=lambda f: f.stat().st_mtime)
    return send_file(latest, mimetype="audio/mpeg")

@app.route("/api/tts/stream")
def api_tts_stream():
    text = request.args.get("text", "").strip()
    if not text:
        return jsonify({"error": "Empty text"}), 400
    voice = request.args.get("voice", config["voice"])
    rate = _normalize_tts_param(request.args.get("rate", config["rate"]), '%')
    volume = _normalize_tts_param(request.args.get("volume", config["volume"]), '%')
    pitch = _normalize_tts_param(request.args.get("pitch", config["pitch"]), 'Hz')

    def generate():
        proc = None
        try:
            proc = tts_engine.generate_stream(text=text, voice=voice, rate=rate, volume=volume, pitch=pitch)
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
            proc.wait()
            if proc.returncode != 0:
                err = proc.stderr.read().decode(errors='ignore') if proc.stderr else ''
                logger.error(f"edge-tts завершился с кодом {proc.returncode}: {err[:200]}")
        except Exception as e:
            logger.error(f"Stream TTS error: {e}")
            yield b""
        finally:
            if proc:
                try:
                    proc.stdout.close()
                    proc.stderr.close()
                    proc.wait(timeout=5)
                except Exception:
                    pass

    return Response(generate(), mimetype="audio/mpeg", headers={"Content-Disposition": "inline", "Cache-Control": "no-cache"})

@app.route("/api/voices")
def api_voices():
    try:
        return jsonify(tts_engine.list_voices())
    except:
        return jsonify([])

@app.route("/api/sse")
def api_sse():
    def event_stream():
        client_queue = queue.Queue(maxsize=20)
        with sse_lock:
            sse_clients.append(client_queue)
        try:
            yield f"data: {json.dumps({'event': 'connected'})}\n\n"
            while True:
                try:
                    msg = client_queue.get(timeout=30)
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            with sse_lock:
                if client_queue in sse_clients:
                    sse_clients.remove(client_queue)
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/api/test_event", methods=["POST"])
def test_event():
    """Универсальный тест событий: follow, subscription, subscription_gift, cheer, raid, reward"""
    data = request.json or {}
    event_type = data.get("type")
    if not event_type:
        return jsonify({"error": "Missing 'type' field"}), 400

    # Преобразуем входные данные в формат, понятный process_event
    event_data = {"type": event_type}

    if event_type == "follow":
        event_data["user"] = data.get("user", "TestFollower")
    elif event_type == "subscription":
        event_data["user"] = data.get("user", "TestSubscriber")
        event_data["tier"] = data.get("tier", "Tier 1")
    elif event_type == "subscription_gift":
        event_data["user"] = data.get("user", "TestGifter")
        event_data["total"] = data.get("total", 5)
    elif event_type == "cheer":
        event_data["user"] = data.get("user", "TestCheerer")
        event_data["bits"] = data.get("bits", 100)
    elif event_type == "raid":
        event_data["user"] = data.get("user", "TestRaidLeader")
        event_data["viewers"] = data.get("viewers", 10)
    elif event_type == "reward":
        event_data["user"] = data.get("user", "TestUser")
        event_data["reward_name"] = data.get("reward_name", "Тестовая награда")
        event_data["message"] = data.get("message", "")
    else:
        return jsonify({"error": f"Unknown event type: {event_type}"}), 400

    logger.info(f"🧪 ТЕСТОВОЕ СОБЫТИЕ: {event_data}")
    process_event(event_data)
    return jsonify({"status": f"Event {event_type} processed"})

@app.route("/api/debug/config", methods=["GET"])
def debug_config():
    """Отладка: показать текущую конфигурацию события reward"""
    reward_cfg = config.get("events", {}).get("reward", {})
    return jsonify({
        "reward_enabled": reward_cfg.get("enabled"),
        "format_no_msg": reward_cfg.get("format_no_msg"),
        "format_with_msg": reward_cfg.get("format_with_msg"),
        "voice": reward_cfg.get("voice"),
        "reward_voice_map": reward_cfg.get("reward_voice_map", {})
    })

@app.route("/api/twitch/start", methods=["POST"])
def twitch_start():
    return jsonify({"error": "Manual start disabled, bot starts automatically"}), 400

@app.route("/api/twitch/stop", methods=["POST"])
def twitch_stop():
    global twitch_bot, twitch_running
    if not twitch_running:
        return jsonify({"status": "not_running"})
    try:
        if twitch_bot:
            twitch_bot.stop()
        stop_event_sub()
        twitch_running = False
        logger.info("🔌 Bot stopped")
        log_to_queue("system", "Бот отключён")
        return jsonify({"status": "stopped"})
    except Exception as e:
        logger.error(f"❌ Stop error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/twitch/test", methods=["POST"])
def twitch_test():
    if not twitch_running or not twitch_bot:
        return jsonify({"error": "Bot not running"}), 400
    data = request.json or {}
    message = data.get("message", "🔊 Test message")
    if twitch_bot.send_message(message):
        return jsonify({"status": "sent"})
    else:
        return jsonify({"error": "Failed to send"}), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Server error"}), 500

def print_banner():
    banner = f"""
╔══════════════════════════════════════════════════════════╗
║  🎙️  Twitch TTS Server v7.9.2 (Унифицированные награды)║
║  🌐 Web GUI:  http://{config['host']}:{config['port']}                    ║
║  🎙️  TTS:      edge-tts ({config['voice']})          ║
║  💾 Save mode: {'ON' if config.get('save_audio', True) else 'OFF (streaming)'}   ║
╚══════════════════════════════════════════════════════════╝
"""
    print(banner)
    logger.info("🚀 Server starting...")

if __name__ == "__main__":
    print_banner()
    auto_start_twitch()
    try:
        app.run(
            host=config["host"],
            port=config["port"],
            debug=False,
            threaded=True
        )
    except KeyboardInterrupt:
        logger.info("👋 Shutting down...")
    finally:
        if twitch_running and twitch_bot:
            twitch_bot.stop()
        stop_event_sub()
        logger.info("✅ Server stopped")