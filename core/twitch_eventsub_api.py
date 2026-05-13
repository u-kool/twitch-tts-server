# core/twitch_eventsub_api.py
import asyncio
import json
import logging
import traceback
import websockets
import aiohttp

logger = logging.getLogger(__name__)

class TwitchEventSubClient:
    def __init__(self, client_id: str, client_secret: str,
                 access_token: str, refresh_token: str,
                 user_id: str, callback, token_refresher=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.user_id = user_id
        self.callback = callback
        self.token_refresher = token_refresher
        self.websocket = None
        self.session_id = None
        self.running = False
        self.keepalive_task = None
        self.reconnect_required = False

    async def start(self):
        self.running = True
        while self.running:
            try:
                ws_url = 'wss://eventsub.wss.twitch.tv/ws'
                async with websockets.connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5
                ) as ws:
                    self.websocket = ws
                    logger.info("WebSocket подключён, ожидание приветствия...")
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if data['metadata']['message_type'] == 'session_welcome':
                        self.session_id = data['payload']['session']['id']
                        keepalive_timeout = data['payload']['session'].get('keepalive_timeout_seconds', 30)
                        logger.info(f"✅ Сессия EventSub создана: {self.session_id} (keepalive: {keepalive_timeout}s)")
                        await self._subscribe_all()
                        self.keepalive_task = asyncio.create_task(self._keepalive_ping())
                        async for message in ws:
                            await self._handle_message(message)
                            if self.reconnect_required:
                                self.reconnect_required = False
                                logger.info("Переподключение EventSub по запросу...")
                                break
                    else:
                        logger.error(f"Неожиданное приветственное сообщение: {data}")
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket соединение закрыто: {e}")
            except Exception as e:
                logger.error(f"❌ EventSub ошибка: {e}\n{traceback.format_exc()}")
            if self.running:
                logger.info("Повторное подключение через 5 секунд...")
                await asyncio.sleep(5)
            else:
                break
        await self.stop()

    async def _subscribe_all(self):
        events = [
            ('channel.follow', '2', {'moderator_user_id': self.user_id, 'broadcaster_user_id': self.user_id}),
            ('channel.subscribe', '1', {'broadcaster_user_id': self.user_id}),
            ('channel.subscription.gift', '1', {'broadcaster_user_id': self.user_id}),
            ('channel.cheer', '1', {'broadcaster_user_id': self.user_id}),
            ('channel.raid', '1', {'to_broadcaster_user_id': self.user_id}),
            ('channel.channel_points_custom_reward_redemption.add', '1', {'broadcaster_user_id': self.user_id}),
        ]
        for event_type, version, condition in events:
            await self._subscribe(event_type, version, condition)

    async def _subscribe(self, event_type, version, condition):
        for attempt in range(2):
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {
                        'Authorization': f'Bearer {self.access_token}',
                        'Client-Id': self.client_id,
                        'Content-Type': 'application/json'
                    }
                    payload = {
                        'type': event_type,
                        'version': version,
                        'condition': condition,
                        'transport': {
                            'method': 'websocket',
                            'session_id': self.session_id
                        }
                    }
                    async with session.post(
                        'https://api.twitch.tv/helix/eventsub/subscriptions',
                        headers=headers,
                        json=payload
                    ) as resp:
                        if resp.status == 202:
                            logger.info(f"📡 Подписался: {event_type}")
                            return
                        elif resp.status == 401 and attempt == 0 and self.token_refresher:
                            logger.warning(f"❌ Токен истёк при подписке {event_type}, обновляю...")
                            new_token = await self.token_refresher()
                            if new_token:
                                self.access_token = new_token
                                logger.info(f"✅ Токен обновлён, повторная подписка {event_type}")
                                continue
                            else:
                                logger.error("❌ Не удалось обновить токен")
                                return
                        else:
                            text = await resp.text()
                            logger.error(f"❌ Ошибка подписки {event_type}: {resp.status} - {text}")
                            return
            except aiohttp.ClientResponseError as e:
                if attempt == 0 and e.status == 401 and self.token_refresher:
                    logger.warning(f"401 при подписке {event_type}, обновляю токен...")
                    new_token = await self.token_refresher()
                    if new_token:
                        self.access_token = new_token
                        continue
                logger.error(f"HTTP ошибка при подписке {event_type}: {e.status} {e.message}")
                return
            except Exception as e:
                logger.error(f"Исключение при подписке {event_type}: {e}")
                return

    async def _handle_message(self, message):
        try:
            data = json.loads(message)
            msg_type = data['metadata']['message_type']
            if msg_type == 'session_welcome':
                logger.info("Получено новое приветствие сессии")
            elif msg_type == 'notification':
                event_data = data['payload']['event']
                subscription_type = data['metadata']['subscription_type']
                await self._process_event(subscription_type, event_data)
            elif msg_type == 'session_reconnect':
                logger.warning("Получен запрос на переподключение")
                self.reconnect_required = True
            elif msg_type == 'revocation':
                logger.warning("Подписка отозвана")
            else:
                logger.debug(f"Неизвестный тип сообщения: {msg_type}")
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}\n{traceback.format_exc()}")

    async def _process_event(self, event_type, event):
        try:
            if event_type == 'channel.follow':
                data = {
                    "type": "follow",
                    "user": event.get('user_name', 'Аноним')
                }
                logger.info(f"🎉 Событие: {event_type} от {data['user']}")
                self.callback("event", data)
            elif event_type == 'channel.subscribe':
                tier = event.get('tier', '1000')
                plan = {"1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3", "prime": "Prime"}.get(tier, tier)
                data = {
                    "type": "subscription",
                    "user": event.get('user_name', 'Аноним'),
                    "tier": plan,
                    "is_gift": False
                }
                logger.info(f"🎁 Подписка: {data['user']} ({data['tier']})")
                self.callback("event", data)
            elif event_type == 'channel.subscription.gift':
                total = event.get('total', 0)
                data = {
                    "type": "subscription_gift",
                    "user": event.get('user_name', 'Аноним'),
                    "total": total
                }
                logger.info(f"🎁 Подарочная подписка: {data['user']} подарил {total}")
                self.callback("event", data)
            elif event_type == 'channel.cheer':
                bits = event.get('bits', 0)
                data = {
                    "type": "cheer",
                    "user": event.get('user_name', 'Аноним'),
                    "bits": bits
                }
                logger.info(f"💎 Битсы: {data['user']} отправил {bits}")
                self.callback("event", data)
            elif event_type == 'channel.raid':
                from_user = event.get('from_broadcaster_user_name', 'Неизвестный')
                viewers = event.get('viewers', 0)
                data = {
                    "type": "raid",
                    "user": from_user,
                    "viewers": viewers
                }
                logger.info(f"🚀 Рейд: {data['user']} привёл {viewers}")
                self.callback("event", data)
            elif event_type == 'channel.channel_points_custom_reward_redemption.add':
                reward = event.get('reward', {})
                title = reward.get('title', 'награда')
                user_input = event.get('user_input', '').strip()
                
                # ОТЛАДКА: выводим полные данные события
                logger.info(f"🐞 RAW reward event: {event}")
                
                data = {
                    "type": "reward",
                    "user": event.get('user_name', 'Аноним'),
                    "reward_name": title,
                    "message": user_input
                }
                logger.info(f"🎖️ Награда: {data['user']} использовал {title} (сообщение: '{user_input}')")
                self.callback("event", data)
            else:
                logger.debug(f"Неизвестный тип события: {event_type} (данные: {event})")
        except Exception as e:
            logger.error(f"Ошибка при обработке события {event_type}: {e}\n{traceback.format_exc()}")

    async def _keepalive_ping(self):
        try:
            while self.running and self.websocket:
                try:
                    await self.websocket.ping()
                    await asyncio.sleep(10)
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as e:
                    logger.debug(f"Keepalive error: {e}")
                    break
        except asyncio.CancelledError:
            pass

    async def stop(self):
        self.running = False
        if self.keepalive_task:
            self.keepalive_task.cancel()
            try:
                await self.keepalive_task
            except asyncio.CancelledError:
                pass
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception:
                pass
        logger.info("🔌 EventSub остановлен")