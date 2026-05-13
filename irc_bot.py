# irc_bot.py
import socket
import ssl
import threading
import re
import logging
import time

logger = logging.getLogger(__name__)

class TwitchIRCBot:
    def __init__(self, token, nick, channel, tts_callback=None):
        self.token = token
        self.nick = nick.lower()
        self.channel = channel.lower()
        self.tts_callback = tts_callback
        self.server = "irc.chat.twitch.tv"
        self.port = 6697
        self.sock = None
        self.running = False
        self.thread = None
        self._connected = False
        self._connect_event = threading.Event()
        self.emote_sets = []

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._connect_and_listen, daemon=True)
        self.thread.start()
        logger.info(f"IRC bot started for {self.channel}")

    def stop(self):
        self.running = False
        self._connected = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        logger.info("IRC bot stopped")

    def is_connected(self):
        return self._connected

    def wait_connected(self, timeout=10):
        return self._connect_event.wait(timeout)

    def send_message(self, message):
        if not self.sock or not self.running or not self._connected:
            return False
        try:
            msg = f"PRIVMSG {self.channel} :{message}\r\n"
            self.sock.send(msg.encode())
            return True
        except Exception as e:
            logger.error(f"Send error: {e}")
            return False

    def _connect_and_listen(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            context = ssl.create_default_context()
            self.sock = context.wrap_socket(sock, server_hostname=self.server)
            self.sock.settimeout(5.0)
            self.sock.connect((self.server, self.port))

            self.sock.send(b"CAP REQ :twitch.tv/tags\r\n")
            self.sock.send(f"PASS oauth:{self.token}\r\n".encode())
            self.sock.send(f"NICK {self.nick}\r\n".encode())
            self.sock.send(f"JOIN {self.channel}\r\n".encode())

            buffer = ""
            while self.running:
                try:
                    data = self.sock.recv(2048).decode(errors="ignore")
                    if not data:
                        break
                    buffer += data
                    while "\r\n" in buffer:
                        line, buffer = buffer.split("\r\n", 1)
                        self._handle_line(line)
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        logger.error(f"IRC recv error: {e}")
                    break
        except Exception as e:
            logger.error(f"IRC connection error: {e}")
        finally:
            self._connected = False
            if self.sock:
                try:
                    self.sock.close()
                except:
                    pass
            self.running = False

    def _handle_line(self, line: str):
        if line.startswith("PING"):
            try:
                self.sock.send("PONG :tmi.twitch.tv\r\n".encode())
            except:
                pass
            return

        # Парсинг тегов (всегда, чтобы получить tags из JOIN и PRIVMSG)
        tags = {}
        raw_line = line
        if line.startswith('@'):
            parts = line.split(' ', 1)
            tag_part = parts[0][1:]
            for tag in tag_part.split(';'):
                if '=' in tag:
                    k, v = tag.split('=', 1)
                    tags[k] = v
                else:
                    tags[tag] = None
            line = parts[1] if len(parts) > 1 else ''

        # GLOBALUSERSTATE / USERSTATE — содержит emote-sets (все наборы смайлов юзера)
        if 'GLOBALUSERSTATE' in raw_line:
            es = tags.get('emote-sets', '')
            logger.info(f"GLOBALUSERSTATE tags: emote-sets='{es}', all tags keys={list(tags.keys())}")
            if es:
                self.emote_sets = [s for s in es.split(',') if s and s != '0']
                logger.info(f"Got emote sets from GLOBALUSERSTATE: {self.emote_sets}")
            return
        if 'USERSTATE' in raw_line:
            es = tags.get('emote-sets', '')
            if es:
                self.emote_sets = [s for s in es.split(',') if s and s != '0']
                logger.info(f"Got emote sets from USERSTATE: {self.emote_sets}")
            return

        # Обработка успешного присоединения к каналу
        if f"JOIN {self.channel}" in raw_line and f":{self.nick}!{self.nick}@{self.nick}.tmi.twitch.tv" in raw_line:
            if not self._connected:
                self._connected = True
                self._connect_event.set()
                logger.info(f"IRC bot successfully joined {self.channel}")
            return

        match = re.match(r":(\w+)!\1@\1\.tmi\.twitch\.tv PRIVMSG #\w+ :(.*)", line)
        if match:
            user = match.group(1)
            text = match.group(2)

            badges = tags.get('badges', '')
            roles = [b.split('/')[0] for b in badges.split(',') if b]
            is_moderator = 'moderator' in roles
            is_vip = 'vip' in roles
            is_subscriber = 'subscriber' in roles
            is_broadcaster = 'broadcaster' in roles

            is_highlighted = tags.get('msg-id') == 'highlighted-message' or tags.get('highlighted') == '1'
            reply_parent_msg_id = tags.get('reply-parent-msg-id')
            is_reply = reply_parent_msg_id is not None
            reply_to_user = tags.get('reply-parent-user-login', '')

            # Парсинг emote_ids из тега emotes (например "25:0-4,12-16/1902:6-10")
            emotes_tag = tags.get('emotes', '')
            emote_ids = []
            emote_positions = {}  # emote_id -> [(start, end), ...]
            if emotes_tag:
                for part in emotes_tag.split('/'):
                    if ':' not in part:
                        continue
                    eid, positions_str = part.split(':', 1)
                    if not eid:
                        continue
                    emote_ids.append(eid)
                    positions = []
                    for pos in positions_str.split(','):
                        if '-' in pos:
                            try:
                                s, e = pos.split('-', 1)
                                positions.append((int(s), int(e)))
                            except ValueError:
                                pass
                    if positions:
                        emote_positions[eid] = positions

            if self.tts_callback:
                self.tts_callback({
                    "type": "chat",
                    "user": user,
                    "text": text,
                    "badges": roles,
                    "is_moderator": is_moderator,
                    "is_vip": is_vip,
                    "is_subscriber": is_subscriber,
                    "is_broadcaster": is_broadcaster,
                    "is_highlighted": is_highlighted,
                    "is_reply": is_reply,
                    "reply_to_user": reply_to_user,
                    "emote_ids": emote_ids,
                    "emote_positions": emote_positions
                })