"""Version-pinned acquisition with resumable staging and independent checksums."""

import hashlib
import json
import concurrent.futures
from http.client import IncompleteRead
import os
import re
import shutil
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .common import Blocked, quota, read_json, require_compute, safe_relative, sha256, utc_now, write_json
from .discovery import HTTPSRedirects


def local_path(root, relative):
    candidate = root.joinpath(*safe_relative(relative).parts)
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("File path escapes storage root")
    if candidate.is_symlink():
        raise ValueError("Source/destination cannot be a symlink")
    return candidate


def verify(path, item):
    before = path.stat()
    expected_size = item["size_bytes"]
    if not isinstance(expected_size, int) or expected_size < 0 or path.stat().st_size != expected_size:
        raise ValueError("File size mismatch")
    digest = item["source_checksum"]
    algorithm = digest["algorithm"]
    if algorithm not in {"md5", "sha256", "git_blob_sha1"}:
        raise ValueError("Unsupported source checksum")
    source_hash = hashlib.sha1() if algorithm == "git_blob_sha1" else hashlib.new(algorithm)
    if algorithm == "git_blob_sha1":
        source_hash.update(f"blob {expected_size}\0".encode())
    content_hash = hashlib.sha256()
    with path.open("rb") as stream:
        prefix = stream.read(256)
        if prefix.startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise ValueError("Git LFS pointer is not source content")
        if path.suffix.lower() not in {".html", ".htm", ".md", ".txt"} and re.match(br"\s*<(?:!doctype html|html)", prefix, re.I):
            raise ValueError("HTML returned in place of a data file")
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            source_hash.update(chunk)
            content_hash.update(chunk)
    if source_hash.hexdigest() != digest["value"]:
        raise ValueError("Source checksum mismatch")
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("Source changed during checksum verification")
    return {"sha256": content_hash.hexdigest(), "source_checksum_verified": True, "source_checksum": digest, "size_bytes": expected_size,
            "file_fingerprint": {"inode": after.st_ino, "device": after.st_dev, "mtime_ns": after.st_mtime_ns, "size_bytes": after.st_size}}


def reuse_verified_receipt(path, item, record):
    """Reuse the acquisition checksum, with identity checks, instead of hashing twice.

    Legacy receipts from the initial running jobs lack inode metadata. Their
    private raw path, size and last-write-before-verification are checked and
    explicitly marked; extraction still checks CRC for every selected member.
    """
    current = path.stat()
    if record.get("status") != "verified" or record.get("source_checksum_verified") is not True or record["path"] != item["path"]:
        raise ValueError("No successful acquisition receipt for archive")
    if current.st_size != item["size_bytes"] or record["size_bytes"] != current.st_size or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
        raise ValueError("Acquisition receipt identity mismatch")
    if record.get("source_checksum") is not None and record["source_checksum"] != item["source_checksum"]:
        raise ValueError("Pinned publisher checksum changed; cached receipt belongs to another source version")
    fingerprint = {"inode": current.st_ino, "device": current.st_dev, "mtime_ns": current.st_mtime_ns, "size_bytes": current.st_size}
    if record.get("file_fingerprint"):
        if fingerprint != record["file_fingerprint"]:
            raise ValueError("Raw archive changed after verification")
        check = "inode_device_size_mtime_match"
    else:
        if current.st_mtime > datetime.fromisoformat(record["verified_at_utc"]).timestamp():
            raise ValueError("Raw archive modified after legacy verification")
        check = "legacy_receipt_private_path_size_mtime; not_a_new_full_hash"
    if record.get("source_checksum") is None:
        verified = verify(path, item)
        if verified["sha256"] != record["sha256"]:
            raise ValueError("Legacy receipt content differs from pinned source checksum")
        check = "legacy_receipt_rehashed_against_pinned_source_checksum"
    return {"sha256": record["sha256"], "size_bytes": current.st_size, "source_checksum_verified": True,
            "source_checksum": item["source_checksum"], "file_fingerprint": fingerprint,
            "integrity_basis": "acquisition_receipt_plus_member_crc", "receipt_reuse_check": check}


def retry_delay(header, attempt):
    if header:
        try:
            seconds = float(header)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError):
                seconds = 2 ** attempt
        # Do not retry sooner than a long Retry-After; defer to a later job.
        if seconds > 60:
            raise Blocked("BLOCKED_RATE_LIMIT: Retry-After exceeds this attempt's wait budget")
        return max(0, seconds)
    return min(60, 2 ** attempt)


def fetch_http(item, path, max_retries=5):
    state_path = path.with_suffix(path.suffix + ".http.json")
    expected = item["size_bytes"]
    identity = {k: item[k] for k in ["download_url", "size_bytes", "source_checksum"]}
    if path.exists() and path.stat().st_size == expected:
        verify(path, item)
        return
    for attempt in range(max_retries + 1):
        state = read_json(state_path) if state_path.exists() else {}
        offset = path.stat().st_size if path.exists() else 0
        validator = state.get("etag")
        if state.get("identity") != identity or not validator or validator.startswith("W/") or offset >= expected:
            offset = 0
        headers = {"User-Agent": "PlantAgentBench-download/0.1", "Accept-Encoding": "identity"}
        if offset:
            headers.update(Range=f"bytes={offset}-", **{"If-Range": validator})
        try:
            request = Request(item["download_url"], headers=headers)
            with build_opener(HTTPSRedirects()).open(request, timeout=60) as response:
                if response.status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match or int(match[1]) != offset or int(match[3]) != expected or int(match[2]) != expected - 1:
                        raise ValueError("Invalid Content-Range")
                    if offset and response.headers.get("ETag") != validator:
                        raise ValueError("Changed entity on resumed response")
                elif response.status == 200:
                    offset = 0  # Never append a full-body response to partial content.
                else:
                    raise ValueError("Unexpected successful HTTP status")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ValueError("Encoded response cannot be range-verified")
                if "text/html" in response.headers.get("Content-Type", "").lower():
                    raise ValueError("HTML error/login page is not source data")
                write_json(state_path, {"identity": identity, "etag": response.headers.get("ETag")})
                size = offset
                with path.open("ab" if offset else "wb") as stream:
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > expected:
                            raise ValueError("Response larger than pinned file")
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                if size != expected:
                    raise URLError("Incomplete response")
            return
        except HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == max_retries:
                raise Blocked(f"BLOCKED_HTTP_{exc.code}") from None
            time.sleep(retry_delay(exc.headers.get("Retry-After"), attempt))
        except (URLError, TimeoutError, ConnectionError, IncompleteRead):
            if attempt == max_retries:
                raise Blocked("BLOCKED_NETWORK_RETRIES_EXHAUSTED") from None
            time.sleep(retry_delay(None, attempt))


def acquire_item(source, item, raw, staging, network, previous=None):
    """One pinned file; failures remain visible without logging signed URLs."""
    dataset, release = source["dataset_id"], source["release_id"]
    target = local_path(raw, item["path"])
    try:
        if target.exists():
            if previous and previous.get("status") == "verified":
                checked = reuse_verified_receipt(target, item, previous)
                if previous.get("source_checksum") is None:
                    return {**previous, "source_checksum":checked["source_checksum"], "file_fingerprint":checked["file_fingerprint"]}
                return previous
            result = verify(target, item)
        else:
            temporary = local_path(staging, item["path"] + ".part")
            temporary.parent.mkdir(parents=True, exist_ok=True)
            if source["provider"] == "huggingface":
                from huggingface_hub import hf_hub_download
                temporary = Path(hf_hub_download(repo_id=source["repo_id"], repo_type="dataset",
                    revision=release, filename=item["path"], local_dir=str(staging / "hf"),
                    endpoint="https://huggingface.co", token=False))
            else:
                fetch_http(item, temporary, network["max_retries"])
            result = verify(temporary, item)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(temporary, target)
            temporary.unlink()
        return {"dataset_id": dataset, "release_id": release, "path": item["path"],
                "status": "verified", "verified_at_utc": utc_now(), **result}
    except Exception as exc:
        # SDK exceptions include request URLs. Persist only the exception class;
        # a failed file makes the command nonzero, it is never silently accepted.
        reasons = {"File size mismatch": "SIZE_MISMATCH", "Source checksum mismatch": "CHECKSUM_MISMATCH",
                   "Git LFS pointer is not source content": "LFS_POINTER_INSTEAD_OF_CONTENT",
                   "HTML returned in place of a data file": "HTML_INSTEAD_OF_CONTENT",
                   "HTML error/login page is not source data": "HTML_INSTEAD_OF_CONTENT"}
        reason = reasons.get(str(exc), "SOURCE_ACQUISITION_FAILED")
        if isinstance(exc, Blocked) and re.match(r"^BLOCKED_[A-Z0-9_]+(?::|$)", str(exc)):
            reason = str(exc).split(":", 1)[0]
        return {"dataset_id": dataset, "release_id": release, "path": item["path"],
                "status": "failed", "error_type": type(exc).__name__, "reason_code": reason}


def acquire_source(source, items, raw, staging, network, receipt_path):
    """Bounded network concurrency with an append-only per-file checkpoint."""
    import fcntl
    with (staging / ".download.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Blocked("BLOCKED_SOURCE_DOWNLOAD_ALREADY_RUNNING") from None
        previous = {r["path"]: r for r in read_json(receipt_path)} if receipt_path.exists() else {}
        journal_path = staging / "verified_files.jsonl"
        if journal_path.exists():
            # A killed writer can leave one incomplete final line, never a
            # successfully verified raw object with different expected bytes.
            with journal_path.open("r+b") as journal:
                while True:
                    start = journal.tell()
                    line = journal.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        journal.truncate(start)
                        break
                    record = json.loads(line)
                    if record["dataset_id"] != source["dataset_id"] or record["release_id"] != source["release_id"]:
                        raise ValueError("Receipt journal source identity mismatch")
                    previous[record["path"]] = record
        records = {}
        workers = max(1, min(2, network.get("per_source_concurrency", 1), network.get("global_concurrency", 1)))
        with journal_path.open("a") as journal, concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            # map limits active workers. Only metadata futures are buffered;
            # acquired image bytes are streamed to disk by the official SDK.
            results = pool.map(lambda item: acquire_item(source, item, raw, staging, network, previous.get(item["path"])), items)
            for index, record in enumerate(results, 1):
                records[record["path"]] = record
                journal.write(json.dumps(record) + "\n")
                journal.flush()
                if index % 128 == 0:
                    os.fsync(journal.fileno())
                    write_json(receipt_path, [records[k] for k in sorted(records)])
            os.fsync(journal.fileno())
        output = [records[k] for k in sorted(records)]
        write_json(receipt_path, output)
        return output


def download(args, config, root):
    manifests = root / "data/manifests"
    suffix = f".smoke.{args.dataset}" if args.profile == "smoke" else ""
    if args.profile == "smoke" and not args.dataset:
        raise ValueError("smoke requires a dataset")
    plan = read_json(manifests / f"download_plan{suffix}.json")
    if args.dry_run:
        import json
        print(json.dumps({"executed": False, "status": plan["status"], "data_root": plan["data_root"], "blocking_issues": plan["blocking_issues"]}))
        return 3 if plan["blocking_issues"] else 0
    require_compute()
    if plan["status"] != "ready" or plan["blocking_issues"]:
        raise Blocked("BLOCKED_DOWNLOAD_PLAN: resolve storage, inventory and capacity gates first")
    if plan["data_root"] != config["data_root"] or plan["profile"] != args.profile:
        raise ValueError("Plan configuration mismatch")
    for filename, key in [("sources.lock.json", "sources_lock"), ("license_audit.json", "license_audit")]:
        pinned = Path(plan.get(key + "_ref", manifests / filename))
        if sha256(pinned) != plan[key + "_sha256"]:
            raise ValueError("Stale download plan")
    data_root = Path(config["data_root"])
    storage = config["storage"]
    q = quota(storage["quota_mount"], storage.get("quota_kind", "user"), storage.get("quota_id"))
    free = q["available_bytes"]
    required = plan["required_free_bytes_lower_bound"]
    if free is None or min(free, shutil.disk_usage(data_root).free) < required:
        raise Blocked("BLOCKED_STORAGE: current quota is insufficient")
    free_inodes = os.statvfs(data_root).f_favail
    if q.get("available_inodes") is not None:
        free_inodes = min(free_inodes, q["available_inodes"])
    required_inodes = plan.get("required_free_inodes", sum(s["file_count"] for s in plan["sources"]) * 3 + 1024)
    if free_inodes < required_inodes:
        raise Blocked("BLOCKED_INODES: current filesystem or project quota is insufficient")
    failures = []
    selected = [s for s in plan["sources"] if not args.dataset or s["dataset_id"] == args.dataset]
    if not selected:
        raise ValueError("Unknown dataset")
    for source in selected:
        if source["blocking_issues"]:
            failures.append(source["dataset_id"])
            continue
        inventory = Path(source["file_inventory_ref"])
        if sha256(inventory) != source["file_inventory_sha256"]:
            raise ValueError("Inventory changed")
        dataset, release = source["dataset_id"], source["release_id"]
        safe_relative(dataset)
        safe_relative(release)
        raw = local_path(data_root, f"raw/{dataset}/{release}/downloads")
        staging = local_path(data_root, f"staging/{dataset}/{release}")
        raw.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=True, exist_ok=True)
        records = acquire_source(source, read_json(inventory), raw, staging, config["network"],
                                 manifests / f"raw_files.{dataset}.json")
        if any(r["status"] != "verified" for r in records):
            failures.append(dataset)
    return 3 if failures else 0
