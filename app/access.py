import time
import threading
import json
import os
from collections import defaultdict, deque
from typing import Dict, Deque, Optional, Set, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from config import Config


class TokenBucket:
    def __init__(self, rate_kbps: int, capacity: Optional[int] = None):
        self.rate = rate_kbps * 1024
        self.capacity = capacity if capacity else self.rate
        self.tokens = float(self.capacity)
        self.last_refill = time.time()
        self._lock = threading.Lock()

    def _refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

    def consume(self, bytes_needed: int) -> float:
        with self._lock:
            self._refill()
            if self.tokens >= bytes_needed:
                self.tokens -= bytes_needed
                return 0
            deficit = bytes_needed - self.tokens
            wait_time = deficit / self.rate
            self.tokens = 0
            return wait_time


@dataclass
class AccessLog:
    timestamp: float
    ip: str
    user_agent: str
    share_id: str
    email: Optional[str] = None
    success: bool = False
    failure_reason: Optional[str] = None
    bytes_transferred: int = 0
    download_complete: bool = False


class RateLimiter:
    def __init__(self):
        self._failed_attempts: Dict[str, Deque[float]] = defaultdict(deque)
        self._banned_ips: Dict[str, float] = {}
        self._lock = threading.Lock()

    def is_banned(self, ip: str) -> bool:
        with self._lock:
            if ip in self._banned_ips:
                ban_end = self._banned_ips[ip]
                if time.time() < ban_end:
                    return True
                del self._banned_ips[ip]
            return False

    def record_failed_attempt(self, ip: str):
        with self._lock:
            now = time.time()
            attempts = self._failed_attempts[ip]
            attempts.append(now)
            while attempts and now - attempts[0] > Config.BRUTE_FORCE_WINDOW_SECONDS:
                attempts.popleft()
            if len(attempts) >= Config.BRUTE_FORCE_MAX_ATTEMPTS:
                self._banned_ips[ip] = now + Config.BRUTE_FORCE_BAN_SECONDS
                attempts.clear()

    def record_successful_attempt(self, ip: str):
        with self._lock:
            if ip in self._failed_attempts:
                self._failed_attempts[ip].clear()


class AccessController:
    def __init__(self):
        self._concurrent_downloads: Dict[str, Set[str]] = defaultdict(set)
        self._rate_limiters: Dict[Tuple[str, str], TokenBucket] = {}
        self._access_logs: Deque[AccessLog] = deque()
        self._lock = threading.Lock()
        self.rate_limiter = RateLimiter()
        self._load_logs()

    def _load_logs(self):
        try:
            if os.path.exists(Config.ACCESS_LOGS_FILE):
                with open(Config.ACCESS_LOGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for log_data in data:
                        log = AccessLog(
                            timestamp=log_data["timestamp"],
                            ip=log_data["ip"],
                            user_agent=log_data["user_agent"],
                            share_id=log_data["share_id"],
                            email=log_data.get("email"),
                            success=log_data.get("success", False),
                            failure_reason=log_data.get("failure_reason"),
                            bytes_transferred=log_data.get("bytes_transferred", 0),
                            download_complete=log_data.get("download_complete", False),
                        )
                        self._access_logs.append(log)
        except Exception as e:
            print(f"Error loading access logs: {e}")

    def save_logs(self):
        with self._lock:
            try:
                cutoff = time.time() - (Config.STATS_RETENTION_DAYS * 86400)
                recent_logs = [
                    log.__dict__ for log in self._access_logs
                    if log.timestamp > cutoff
                ]
                with open(Config.ACCESS_LOGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(recent_logs, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"Error saving access logs: {e}")

    def log_access(
        self,
        ip: str,
        user_agent: str,
        share_id: str,
        email: Optional[str] = None,
        success: bool = False,
        failure_reason: Optional[str] = None,
        bytes_transferred: int = 0,
        download_complete: bool = False,
    ):
        with self._lock:
            log = AccessLog(
                timestamp=time.time(),
                ip=ip,
                user_agent=user_agent,
                share_id=share_id,
                email=email.strip().lower() if email else None,
                success=success,
                failure_reason=failure_reason,
                bytes_transferred=bytes_transferred,
                download_complete=download_complete,
            )
            self._access_logs.append(log)

    def acquire_download_slot(self, share_id: str, ip: str, max_concurrent: int) -> bool:
        with self._lock:
            key = share_id
            current = self._concurrent_downloads[key]
            if len(current) >= max_concurrent and ip not in current:
                return False
            current.add(ip)
            return True

    def release_download_slot(self, share_id: str, ip: str):
        with self._lock:
            key = share_id
            if ip in self._concurrent_downloads[key]:
                self._concurrent_downloads[key].remove(ip)
            if not self._concurrent_downloads[key]:
                del self._concurrent_downloads[key]

    def get_token_bucket(self, share_id: str, ip: str, speed_kbps: int) -> TokenBucket:
        with self._lock:
            key = (share_id, ip)
            if key not in self._rate_limiters:
                self._rate_limiters[key] = TokenBucket(speed_kbps)
            return self._rate_limiters[key]

    def get_logs_for_share(self, share_id: str) -> list:
        with self._lock:
            return [log for log in self._access_logs if log.share_id == share_id]

    def get_all_logs(self) -> list:
        with self._lock:
            return list(self._access_logs)

    def cleanup_old_logs(self):
        with self._lock:
            cutoff = time.time() - (Config.STATS_RETENTION_DAYS * 86400)
            while self._access_logs and self._access_logs[0].timestamp < cutoff:
                self._access_logs.popleft()


access_controller = AccessController()
