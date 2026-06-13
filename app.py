import os
import io
import time
import mimetypes
from datetime import datetime

from flask import Flask, request, jsonify, render_template, Response, send_file, redirect, url_for, session
from flask import stream_with_context
from apscheduler.schedulers.background import BackgroundScheduler
from zipstream import ZipFile

from config import Config
from app.share import share_manager
from app.access import access_controller
from app.watermark import watermark_service
from app.stats import stats_manager
from app.qrcode_gen import qrcode_generator

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "sharelink-secret-key-change-in-production")
app.permanent_session_lifetime = 3600


def get_client_ip():
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    return request.remote_addr


def get_user_agent():
    return request.headers.get("User-Agent", "")


@app.route("/api/shares", methods=["POST"])
def create_share():
    data = request.get_json(force=True, silent=True) or {}

    file_ids = data.get("file_ids") or data.get("file_id")
    if not file_ids:
        return jsonify({"error": "file_id or file_ids is required"}), 400
    if isinstance(file_ids, (str, int)):
        file_ids = [str(file_ids)]
    else:
        file_ids = [str(fid) for fid in file_ids]

    for fid in file_ids:
        if not watermark_service.file_exists(fid):
            return jsonify({"error": f"File {fid} not found"}), 404

    password = data.get("password")
    expires_in = data.get("expires_in")
    max_downloads = data.get("max_downloads")
    created_by = data.get("created_by", "anonymous")
    allowed_emails = data.get("allowed_emails")
    watermark = data.get("watermark", False)
    max_concurrent_per_ip = data.get("max_concurrent_per_ip", Config.DEFAULT_MAX_CONCURRENT_PER_IP)
    download_speed_kbps = data.get("download_speed_kbps", Config.DEFAULT_DOWNLOAD_SPEED_KBPS)

    share = share_manager.create_share(
        file_ids=file_ids,
        password=password,
        expires_in=expires_in,
        max_downloads=max_downloads,
        created_by=created_by,
        allowed_emails=allowed_emails,
        watermark=watermark,
        max_concurrent_per_ip=max_concurrent_per_ip,
        download_speed_kbps=download_speed_kbps,
    )

    share_url = f"{request.scheme}://{request.host}/s/{share.share_id}"

    result = share.to_dict()
    result.pop("password_hash", None)
    result["share_url"] = share_url
    result["qrcode_url"] = f"{request.scheme}://{request.host}/api/shares/{share.share_id}/qrcode"

    return jsonify(result), 201


@app.route("/api/shares", methods=["GET"])
def list_shares():
    include_archived = request.args.get("include_archived", "false").lower() == "true"
    shares = share_manager.list_shares(include_archived=include_archived)
    result = []
    for share in shares:
        d = share.to_dict()
        d.pop("password_hash", None)
        d["share_url"] = f"{request.scheme}://{request.host}/s/{share.share_id}"
        d["is_expired"] = share.is_expired()
        result.append(d)
    return jsonify(result)


@app.route("/api/shares/<share_id>", methods=["GET"])
def get_share(share_id):
    share = share_manager.get_share_include_archived(share_id)
    if not share:
        return jsonify({"error": "Share not found"}), 404

    result = share.to_dict()
    result.pop("password_hash", None)
    result["share_url"] = f"{request.scheme}://{request.host}/s/{share.share_id}"
    result["is_expired"] = share.is_expired()

    return jsonify(result)


@app.route("/api/shares/<share_id>/stats", methods=["GET"])
def get_share_stats(share_id):
    share = share_manager.get_share_include_archived(share_id)
    if not share:
        return jsonify({"error": "Share not found"}), 404

    stats = stats_manager.get_share_stats(share_id)
    return jsonify(stats)


@app.route("/api/shares/<share_id>/qrcode", methods=["GET"])
def get_share_qrcode(share_id):
    share = share_manager.get_share_include_archived(share_id)
    if not share:
        return jsonify({"error": "Share not found"}), 404

    size = request.args.get("size", 300, type=int)
    size = min(max(size, 100), 1000)

    share_url = f"{request.scheme}://{request.host}/s/{share_id}"
    qr_bytes = qrcode_generator.generate_qrcode(share_url, size=size)

    return Response(
        qr_bytes,
        mimetype="image/png",
        headers={"Content-Disposition": f'inline; filename="qrcode_{share_id}.png"'}
    )


@app.route("/api/stats/top-files", methods=["GET"])
def get_top_files():
    limit = request.args.get("limit", 10, type=int)
    result = stats_manager.get_top_files(limit=limit)
    return jsonify(result)


@app.route("/api/stats/peak-hours", methods=["GET"])
def get_peak_hours():
    result = stats_manager.get_download_peak_hours()
    return jsonify(result)


@app.route("/api/stats/creator/<created_by>", methods=["GET"])
def get_creator_stats(created_by):
    result = stats_manager.get_creator_stats(created_by)
    return jsonify(result)


@app.route("/s/<share_id>", methods=["GET"])
def share_page(share_id):
    ip = get_client_ip()
    ua = get_user_agent()

    if access_controller.rate_limiter.is_banned(ip):
        access_controller.log_access(ip, ua, share_id, success=False, failure_reason="ip_banned")
        return render_template("error.html", message="访问过于频繁，请稍后再试", status=429), 429

    share = share_manager.get_share(share_id)

    if not share:
        share_archived = share_manager.get_share_include_archived(share_id)
        if share_archived:
            access_controller.rate_limiter.record_failed_attempt(ip)
            access_controller.log_access(ip, ua, share_id, success=False, failure_reason="share_expired")
            return render_template("expired.html"), 410
        else:
            access_controller.rate_limiter.record_failed_attempt(ip)
            access_controller.log_access(ip, ua, share_id, success=False, failure_reason="share_not_found")
            return render_template("error.html", message="分享链接不存在", status=404), 404

    access_controller.rate_limiter.record_successful_attempt(ip)

    needs_password = bool(share.password_hash)
    needs_email = bool(share.allowed_emails)

    return render_template(
        "share_page.html",
        share_id=share_id,
        needs_password=needs_password,
        needs_email=needs_email,
        file_count=len(share.file_ids),
        watermark=share.watermark,
        expires_at=share.expires_at,
        max_downloads=share.max_downloads,
        download_count=share.download_count,
    )


@app.route("/s/<share_id>/verify", methods=["POST"])
def verify_share(share_id):
    ip = get_client_ip()
    ua = get_user_agent()

    if access_controller.rate_limiter.is_banned(ip):
        return jsonify({"error": "访问过于频繁，请稍后再试"}), 429

    share = share_manager.get_share(share_id)
    if not share:
        return jsonify({"error": "分享链接已过期或不存在"}), 410

    if share.password_hash:
        password = request.form.get("password") or (request.get_json(silent=True) or {}).get("password")
        if not password or not share.check_password(password):
            access_controller.log_access(ip, ua, share_id, success=False, failure_reason="wrong_password")
            return jsonify({"error": "密码错误"}), 401

    email = None
    if share.allowed_emails:
        email = request.form.get("email") or (request.get_json(silent=True) or {}).get("email")
        if not email or not share.check_email(email):
            access_controller.log_access(ip, ua, share_id, email=email, success=False, failure_reason="email_not_allowed")
            return jsonify({"error": "邮箱不在白名单内"}), 403

    session.permanent = True
    verified_shares = session.get("verified_shares", {})
    verified_shares[share_id] = {
        "email": email,
        "verified_at": time.time(),
    }
    session["verified_shares"] = verified_shares

    download_url = url_for("download_share", share_id=share_id, _external=True)

    access_controller.log_access(ip, ua, share_id, email=email, success=True)
    return jsonify({"download_url": download_url})


@app.route("/s/<share_id>/download", methods=["GET"])
def download_share(share_id):
    ip = get_client_ip()
    ua = get_user_agent()

    if access_controller.rate_limiter.is_banned(ip):
        access_controller.log_access(ip, ua, share_id, success=False, failure_reason="ip_banned")
        return "访问过于频繁，请稍后再试", 429

    share = share_manager.get_share(share_id)
    if not share:
        access_controller.rate_limiter.record_failed_attempt(ip)
        access_controller.log_access(ip, ua, share_id, success=False, failure_reason="share_expired")
        return "分享链接已过期或不存在", 410

    verified_shares = session.get("verified_shares", {})
    share_verified = share_id in verified_shares

    if share.password_hash and not share_verified:
        return redirect(url_for("share_page", share_id=share_id))

    email = None
    if share.allowed_emails:
        if share_verified:
            email = verified_shares[share_id].get("email")
        else:
            email = request.args.get("email")
        if not email or not share.check_email(email):
            return redirect(url_for("share_page", share_id=share_id))

    if not access_controller.acquire_download_slot(share_id, ip, share.max_concurrent_per_ip):
        access_controller.log_access(ip, ua, share_id, email=email, success=False, failure_reason="too_many_concurrent")
        return "下载并发数超限，请稍后再试", 429

    try:
        share_manager.increment_download(share_id)

        token_bucket = access_controller.get_token_bucket(share_id, ip, share.download_speed_kbps)

        file_ids = share.file_ids
        use_watermark = share.watermark

        if len(file_ids) == 1:
            return _download_single_file(share, file_ids[0], use_watermark, email, ip, ua, token_bucket)
        else:
            return _download_zip_files(share, file_ids, use_watermark, email, ip, ua, token_bucket)

    finally:
        access_controller.release_download_slot(share_id, ip)


def _rate_limited_stream(generator, token_bucket, share_id, ip, ua, email):
    bytes_transferred = 0
    try:
        for chunk in generator:
            if not chunk:
                continue
            wait_time = token_bucket.consume(len(chunk))
            if wait_time > 0:
                time.sleep(wait_time)
            bytes_transferred += len(chunk)
            yield chunk
        access_controller.log_access(
            ip, ua, share_id, email=email, success=True,
            bytes_transferred=bytes_transferred, download_complete=True
        )
    except GeneratorExit:
        access_controller.log_access(
            ip, ua, share_id, email=email, success=True,
            bytes_transferred=bytes_transferred, download_complete=False
        )
        raise
    except Exception:
        access_controller.log_access(
            ip, ua, share_id, email=email, success=False,
            bytes_transferred=bytes_transferred, failure_reason="download_error"
        )
        raise


def _download_single_file(share, file_id, use_watermark, email, ip, ua, token_bucket):
    file_path = watermark_service.get_file_path(file_id)
    original_filename = watermark_service.get_original_filename(file_id)

    if use_watermark:
        stream, output_filename = watermark_service.add_watermark_stream(file_path, original_filename, email)
    else:
        def file_stream():
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    yield chunk
        stream = file_stream()
        output_filename = original_filename

    mimetype, _ = mimetypes.guess_type(output_filename)
    if not mimetype:
        mimetype = "application/octet-stream"

    limited_stream = _rate_limited_stream(stream, token_bucket, share.share_id, ip, ua, email)

    response = Response(
        stream_with_context(limited_stream),
        mimetype=mimetype,
    )
    response.headers["Content-Disposition"] = f'attachment; filename="{output_filename}"'
    if os.path.exists(file_path):
        file_size = os.path.getsize(file_path)
        if not use_watermark:
            response.headers["Content-Length"] = str(file_size)
    return response


def _download_zip_files(share, file_ids, use_watermark, email, ip, ua, token_bucket):
    zip_filename = f"share_{share.share_id}.zip"

    def zip_stream_generator():
        zf = ZipFile(compression=8, allowZip64=True)

        for idx, file_id in enumerate(file_ids):
            file_path = watermark_service.get_file_path(file_id)
            original_filename = watermark_service.get_original_filename(file_id)

            if use_watermark:
                data, output_filename = watermark_service.add_watermark(file_path, original_filename, email)
                arcname = f"file_{idx + 1}{os.path.splitext(output_filename)[1]}"
                zf.write_iter(arcname, iter([data]))
            else:
                arcname = f"file_{idx + 1}{os.path.splitext(original_filename)[1]}"
                zf.write(file_path, arcname)

        for chunk in zf:
            yield chunk

    limited_stream = _rate_limited_stream(zip_stream_generator(), token_bucket, share.share_id, ip, ua, email)

    response = Response(
        stream_with_context(limited_stream),
        mimetype="application/zip",
    )
    response.headers["Content-Disposition"] = f'attachment; filename="{zip_filename}"'
    return response


def periodic_snapshot():
    share_manager.save_snapshot()
    access_controller.save_logs()
    stats_manager.save_data()
    share_manager.cleanup_expired()
    access_controller.cleanup_old_logs()
    stats_manager.cleanup_old_data()


def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        periodic_snapshot,
        "interval",
        minutes=Config.SNAPSHOT_INTERVAL_MINUTES,
        id="snapshot_job",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler


if __name__ == "__main__":
    scheduler = start_scheduler()
    try:
        app.run(
            host="0.0.0.0",
            port=Config.PORT,
            debug=False,
            threaded=True,
        )
    finally:
        periodic_snapshot()
        scheduler.shutdown()
