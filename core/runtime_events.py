import copy
import queue
import threading
import time
from collections import deque


class RuntimeEvents:
    def __init__(self, max_logs: int = 200, max_client_queue: int = 20):
        self.message_log = deque(maxlen=max_logs)
        self.message_log_lock = threading.Lock()
        self.sse_clients = []
        self.sse_lock = threading.Lock()
        self.max_client_queue = max_client_queue

    def log(self, msg_type: str, text: str, user: str = None, emotes: dict = None):
        entry = {
            "type": msg_type,
            "user": user,
            "text": text,
            "timestamp": time.time(),
        }
        if emotes:
            entry["emotes"] = emotes
        with self.message_log_lock:
            self.message_log.append(entry)

    def logs(self, limit: int = 50):
        with self.message_log_lock:
            return copy.deepcopy(list(self.message_log)[-limit:])

    def log_count(self) -> int:
        with self.message_log_lock:
            return len(self.message_log)

    def add_sse_client(self):
        client_queue = queue.Queue(maxsize=self.max_client_queue)
        with self.sse_lock:
            self.sse_clients.append(client_queue)
        return client_queue

    def remove_sse_client(self, client_queue):
        with self.sse_lock:
            if client_queue in self.sse_clients:
                self.sse_clients.remove(client_queue)

    def broadcast(self, message: dict):
        with self.sse_lock:
            clients = list(self.sse_clients)
        for client_queue in clients:
            try:
                client_queue.put_nowait(message)
            except queue.Full:
                pass
