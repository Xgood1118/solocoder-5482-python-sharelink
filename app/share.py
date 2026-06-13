import os
import json
import time
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from nanoid import generate as nanoid_generate
import bcrypt

from config import Config


class Share:
    def __init__(
        self,
        share_id: str,
        file_ids: List[str],
        password_hash: Optional[str] = None,
        expires_at: Optional[float] = None,
        max_downloads: Optional[int] = None,
        created_by: str = "anonymous",
        allowed_emails: Optional[List[str]] = None,
        watermark: bool = False,
        max_concurrent_per_ip: int = Config.DEFAULT_MAX_CONCURRENT_PER_IP,
        download_speed_kbps: int = Config.DEFAULT_DOWNLOAD_SPEED_KBPS,
    ):
        self.share_id = share_id
        self.file_ids = file_ids
        self.password_hash = password_hash
        self.expires_at = expires_at
        self.max_downloads = max_downloads
        self.download_count = 0
        self.created_at = time.time()
        self.created_by = created_by
        self.allowed_emails = [e.strip().lower() for e in (allowed_emails or [])]
        self.watermark = watermark
        self.max_concurrent_per_ip = max_concurrent_per_ip
        self.download_speed_kbps = download_speed_kbps
        self.archived = False

    def is_expired(self) -> bool:
        if self.archived:
            return True
        if self.expires_at and time.time() > self.expires_at:
            return True
        if self.max_downloads and self.download_count >= self.max_downloads:
            return True
        return False

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return True
        return bcrypt.checkpw(password.encode("utf-8"), self.password_hash.encode("utf-8"))

    def check_email(self, email: str) -> bool:
        if not self.allowed_emails:
            return True
        normalized_email = email.strip().lower()
        return normalized_email in self.allowed_emails

    def to_dict(self) -> Dict[str, Any]:
        return {
            "share_id": self.share_id,
            "file_ids": self.file_ids,
            "password_hash": self.password_hash,
            "expires_at": self.expires_at,
            "max_downloads": self.max_downloads,
            "download_count": self.download_count,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "allowed_emails": self.allowed_emails,
            "watermark": self.watermark,
            "max_concurrent_per_ip": self.max_concurrent_per_ip,
            "download_speed_kbps": self.download_speed_kbps,
            "archived": self.archived,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Share":
        share = cls(
            share_id=data["share_id"],
            file_ids=data["file_ids"],
            password_hash=data.get("password_hash"),
            expires_at=data.get("expires_at"),
            max_downloads=data.get("max_downloads"),
            created_by=data.get("created_by", "anonymous"),
            allowed_emails=data.get("allowed_emails", []),
            watermark=data.get("watermark", False),
            max_concurrent_per_ip=data.get("max_concurrent_per_ip", Config.DEFAULT_MAX_CONCURRENT_PER_IP),
            download_speed_kbps=data.get("download_speed_kbps", Config.DEFAULT_DOWNLOAD_SPEED_KBPS),
        )
        share.download_count = data.get("download_count", 0)
        share.archived = data.get("archived", False)
        return share


class ShareManager:
    def __init__(self):
        self._shares: Dict[str, Share] = {}
        self._lock = threading.RLock()
        self._load_snapshot()

    def _load_snapshot(self):
        try:
            if os.path.exists(Config.METADATA_SNAPSHOT_FILE):
                with open(Config.METADATA_SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for share_data in data:
                        share = Share.from_dict(share_data)
                        self._shares[share.share_id] = share
        except Exception as e:
            print(f"Error loading snapshot: {e}")

    def save_snapshot(self):
        with self._lock:
            try:
                data = [share.to_dict() for share in self._shares.values()]
                with open(Config.METADATA_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"Error saving snapshot: {e}")

    def create_share(
        self,
        file_ids: List[str],
        password: Optional[str] = None,
        expires_in: Optional[int] = None,
        max_downloads: Optional[int] = None,
        created_by: str = "anonymous",
        allowed_emails: Optional[List[str]] = None,
        watermark: bool = False,
        max_concurrent_per_ip: int = Config.DEFAULT_MAX_CONCURRENT_PER_IP,
        download_speed_kbps: int = Config.DEFAULT_DOWNLOAD_SPEED_KBPS,
    ) -> Share:
        with self._lock:
            while True:
                share_id = nanoid_generate(size=Config.NANOID_LENGTH)
                if share_id not in self._shares:
                    break

            password_hash = None
            if password:
                password_hash = bcrypt.hashpw(
                    password.encode("utf-8"),
                    bcrypt.gensalt(rounds=Config.BCRYPT_ROUNDS)
                ).decode("utf-8")

            expires_at = None
            if expires_in:
                expires_at = time.time() + expires_in

            share = Share(
                share_id=share_id,
                file_ids=file_ids,
                password_hash=password_hash,
                expires_at=expires_at,
                max_downloads=max_downloads,
                created_by=created_by,
                allowed_emails=allowed_emails,
                watermark=watermark,
                max_concurrent_per_ip=max_concurrent_per_ip,
                download_speed_kbps=download_speed_kbps,
            )

            self._shares[share_id] = share
            return share

    def get_share(self, share_id: str) -> Optional[Share]:
        share = self._shares.get(share_id)
        if share and not share.archived and share.is_expired():
            self._archive_share(share_id)
            return None
        return share

    def get_share_include_archived(self, share_id: str) -> Optional[Share]:
        return self._shares.get(share_id)

    def increment_download(self, share_id: str):
        with self._lock:
            share = self._shares.get(share_id)
            if share:
                share.download_count += 1
                if share.max_downloads and share.download_count >= share.max_downloads:
                    self._archive_share(share_id)

    def _archive_share(self, share_id: str):
        share = self._shares.get(share_id)
        if share:
            share.archived = True

    def list_shares(self, include_archived: bool = False) -> List[Share]:
        shares = list(self._shares.values())
        if not include_archived:
            shares = [s for s in shares if not s.archived]
        return sorted(shares, key=lambda s: s.created_at, reverse=True)

    def get_shares_by_creator(self, created_by: str) -> List[Share]:
        return [s for s in self._shares.values() if s.created_by == created_by]

    def get_file_share_count(self, file_id: str) -> int:
        return sum(1 for s in self._shares.values() if file_id in s.file_ids)

    def get_top_files(self, limit: int = 10) -> List[tuple]:
        file_counts = {}
        for share in self._shares.values():
            for file_id in share.file_ids:
                file_counts[file_id] = file_counts.get(file_id, 0) + 1
        return sorted(file_counts.items(), key=lambda x: x[1], reverse=True)[:limit]

    def cleanup_expired(self):
        with self._lock:
            now = time.time()
            cutoff = now - (Config.STATS_RETENTION_DAYS * 86400)
            to_delete = []
            for share_id, share in self._shares.items():
                if share.archived and share.created_at < cutoff:
                    to_delete.append(share_id)
            for share_id in to_delete:
                del self._shares[share_id]


share_manager = ShareManager()
