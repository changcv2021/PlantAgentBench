"""Small metadata utilities. Bulk processing belongs in a Slurm allocation."""

import hashlib
import json
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit


class Blocked(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Independent source jobs may export identical schemas simultaneously.
    # A shared '.tmp' filename allows one writer to rename another's file.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=path.name + ".", suffix=".tmp",
                                     dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text())


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_relative(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise ValueError("Unsafe source path")
    if any(ord(c) < 32 for c in name) or str(p) != name or name == ".":
        raise ValueError("Noncanonical source path")
    return p


def clean_url(url):
    parts = urlsplit(url)
    # Do not retain credentials, signed parameters, or fragments in logs.
    return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))


def command(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return {"command": args, "returncode": p.returncode, "stdout": p.stdout[:16000], "stderr": p.stderr[:1000]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": args, "returncode": None, "error_type": type(exc).__name__}


def quota(mount, kind="user", identity=None):
    switches = {"user": "-u", "group": "-g", "project": "-p"}
    if kind not in switches or (kind != "user" and identity is None):
        raise ValueError("Quota kind and identity must be explicit")
    identity = str(os.getuid()) if identity is None else str(identity)
    result = command(["lfs", "quota", switches[kind], identity, mount])
    result["quota_kind"] = kind
    result["quota_id"] = identity
    result["checked_at_utc"] = utc_now()
    result["available_bytes"] = None
    result["available_inodes"] = None
    if result["returncode"] == 0:
        for line in result["stdout"].splitlines():
            fields = line.split()
            if len(fields) >= 9 and fields[0] == mount:
                try:
                    used, soft, hard = [int(x.rstrip("*")) for x in fields[1:4]]
                    files, inode_soft, inode_hard = [int(x.rstrip("*")) for x in fields[5:8]]
                    limits = [x for x in [soft, hard] if x > 0]
                    inode_limits = [x for x in [inode_soft, inode_hard] if x > 0]
                    result.update(used_bytes=used * 1024, hard_limit_bytes=hard * 1024,
                                  available_bytes=max(0, min(limits) - used) * 1024 if limits else None,
                                  available_inodes=max(0, min(inode_limits) - files) if inode_limits else None)
                except ValueError:
                    result["parse_status"] = "unresolved"
    return result


def require_compute():
    host = socket.gethostname().split(".")[0]
    job = os.environ.get("SLURM_JOB_ID")
    if host.startswith("login") or not job or not re.fullmatch(r"\d+", job):
        raise Blocked("BLOCKED_EXECUTION_HOST: bulk acquisition requires a verified Slurm compute allocation")
    details = command(["scontrol", "show", "job", "-o", job])
    if details["returncode"] != 0 or f"({os.getuid()})" not in details.get("stdout", ""):
        raise Blocked("BLOCKED_ALLOCATION_VERIFICATION")
    match = re.search(r"(?:^| )NodeList=(\S+)", details["stdout"])
    if not match or "JobState=RUNNING" not in details["stdout"]:
        raise Blocked("BLOCKED_ALLOCATION_NOT_RUNNING")
    nodes = command(["scontrol", "show", "hostnames", match.group(1)])
    if nodes["returncode"] != 0:
        raise Blocked("BLOCKED_HOST_OUTSIDE_ALLOCATION")
    for node in nodes.get("stdout", "").split():
        node_details = command(["scontrol", "show", "node", "-o", node])
        if node_details["returncode"] != 0:
            continue
        aliases = re.findall(r"(?:^| )(?:NodeName|NodeHostName|NodeAddr)=(\S+)", node_details.get("stdout", ""))
        if host in [alias.split(".")[0] for alias in aliases]:
            return {"host": host, "slurm_node": node, "job_id": job,
                    "verification": "Slurm running allocation owner and authoritative NodeHostName/NodeAddr"}
    raise Blocked("BLOCKED_HOST_OUTSIDE_ALLOCATION")
