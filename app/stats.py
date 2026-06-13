import os
import json
import time
from collections import defaultdict, Counter
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta

from config import Config
from .access import access_controller
from .share import share_manager


class StatsManager:
    def __init__(self):
        self._load_data()

    def _load_data(self):
        try:
            if os.path.exists(Config.STATS_DATA_FILE):
                with open(Config.STATS_DATA_FILE, "r", encoding="utf-8") as f:
                    self._persistent_data = json.load(f)
            else:
                self._persistent_data = {}
        except Exception as e:
            print(f"Error loading stats data: {e}")
            self._persistent_data = {}

    def save_data(self):
        try:
            with open(Config.STATS_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._persistent_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving stats data: {e}")

    def get_share_stats(self, share_id: str) -> Dict[str, Any]:
        logs = access_controller.get_logs_for_share(share_id)
        share = share_manager.get_share_include_archived(share_id)

        if not share:
            return {}

        page_views = len([l for l in logs])
        unique_ips = len(set(l.ip for l in logs))
        unique_visitors = unique_ips

        successful_downloads = len([l for l in logs if l.download_complete])
        failed_downloads = len([l for l in logs if l.success and not l.download_complete and l.bytes_transferred > 0])
        total_attempts = successful_downloads + failed_downloads
        completion_rate = (successful_downloads / total_attempts * 100) if total_attempts > 0 else 0

        hourly_distribution = defaultdict(int)
        for log in logs:
            dt = datetime.fromtimestamp(log.timestamp)
            hour_key = dt.strftime("%Y-%m-%d %H:00")
            hourly_distribution[hour_key] += 1

        sorted_hours = sorted(hourly_distribution.items())
        last_24_hours = []
        now = datetime.now()
        for i in range(24):
            hour_time = now - timedelta(hours=23 - i)
            hour_key = hour_time.strftime("%Y-%m-%d %H:00")
            last_24_hours.append({
                "hour": hour_key,
                "count": hourly_distribution.get(hour_key, 0)
            })

        email_distribution = {}
        if share.allowed_emails:
            email_counter = Counter()
            for log in logs:
                if log.email:
                    email_counter[log.email] += 1
            email_distribution = dict(email_counter)

        download_by_hour = defaultdict(int)
        for log in logs:
            if log.download_complete:
                dt = datetime.fromtimestamp(log.timestamp)
                hour_key = dt.strftime("%Y-%m-%d %H:00")
                download_by_hour[hour_key] += 1

        last_7_days_downloads = []
        for i in range(7):
            day_time = now - timedelta(days=6 - i)
            day_key = day_time.strftime("%Y-%m-%d")
            day_count = sum(
                1 for log in logs
                if log.download_complete and datetime.fromtimestamp(log.timestamp).strftime("%Y-%m-%d") == day_key
            )
            last_7_days_downloads.append({
                "date": day_key,
                "count": day_count
            })

        total_bytes = sum(log.bytes_transferred for log in logs if log.download_complete)

        return {
            "share_id": share.share_id,
            "is_expired": share.is_expired(),
            "archived": share.archived,
            "page_views": page_views,
            "unique_ips": unique_ips,
            "unique_visitors": unique_visitors,
            "download_count": share.download_count,
            "max_downloads": share.max_downloads,
            "successful_downloads": successful_downloads,
            "failed_downloads": failed_downloads,
            "completion_rate": round(completion_rate, 2),
            "total_bytes_transferred": total_bytes,
            "hourly_distribution": last_24_hours,
            "downloads_by_day": last_7_days_downloads,
            "email_distribution": email_distribution,
            "created_at": share.created_at,
            "expires_at": share.expires_at,
            "watermark": share.watermark,
            "file_count": len(share.file_ids),
        }

    def get_creator_stats(self, created_by: str) -> Dict[str, Any]:
        shares = share_manager.get_shares_by_creator(created_by)
        share_ids = [s.share_id for s in shares]

        all_logs = [log for log in access_controller.get_all_logs() if log.share_id in share_ids]

        total_shares = len(shares)
        active_shares = len([s for s in shares if not s.is_expired()])
        total_downloads = sum(s.download_count for s in shares)
        total_bytes = sum(log.bytes_transferred for log in all_logs if log.download_complete)

        return {
            "created_by": created_by,
            "total_shares": total_shares,
            "active_shares": active_shares,
            "total_downloads": total_downloads,
            "total_bytes_transferred": total_bytes,
        }

    def get_top_files(self, limit: int = 10) -> List[Dict[str, Any]]:
        top_files = share_manager.get_top_files(limit)
        result = []
        for file_id, share_count in top_files:
            total_downloads = 0
            for share in share_manager.list_shares(include_archived=True):
                if file_id in share.file_ids:
                    total_downloads += share.download_count
            result.append({
                "file_id": file_id,
                "share_count": share_count,
                "total_downloads": total_downloads,
            })
        return result

    def get_download_peak_hours(self) -> List[Dict[str, Any]]:
        all_logs = access_controller.get_all_logs()
        hour_counter = Counter()

        for log in all_logs:
            if log.download_complete:
                dt = datetime.fromtimestamp(log.timestamp)
                hour = dt.hour
                hour_counter[hour] += 1

        result = []
        for hour in range(24):
            result.append({
                "hour": hour,
                "downloads": hour_counter.get(hour, 0),
            })
        return result

    def cleanup_old_data(self):
        cutoff = time.time() - (Config.STATS_RETENTION_DAYS * 86400)
        keys_to_delete = []
        for key in self._persistent_data:
            data = self._persistent_data[key]
            if isinstance(data, dict) and data.get("timestamp", 0) < cutoff:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            del self._persistent_data[key]


stats_manager = StatsManager()
