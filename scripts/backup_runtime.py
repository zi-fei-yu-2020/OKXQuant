"""Custom OKXQuant backup job runtime: archive, encrypt, verify, deliver and retain."""
from __future__ import annotations
import base64
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
LOCAL_DIR = BACKUPS / "local"
SQLITE_DIR = BACKUPS / "sqlite"
MANIFEST_DIR = BACKUPS / "manifests"
BJ_TZ = timezone(timedelta(hours=8))
MAGIC = b"OKXQuantGCM2\x00"
MANDATORY_EXCLUDES = (".git/**", ".env", ".okx/**", ".bypy/**", "backups/**", "logs/**", "data/okxquant_admin.db*", "data/llm_models.json", "data/llm_providers.json", "data/*.enc", "data/.*_key", "data/credentials/**", "data/*.db-wal", "data/*.db-shm", "**/__pycache__/**", "*.pyc")
SCOPE_PATHS = {
    "data": ("data",), "scripts": ("scripts",), "dashboard": ("dashboard",), "okxquant_backend": ("okxquant_backend",), "okxquant_gateway": ("okxquant_gateway",),
    "tests": ("tests",), "recovery_guide": ("RECOVERY_GUIDE.md",), "agent_profile": ("SOUL.md", "PROFILE.md", "AGENTS.md", "MEMORY.md"),
    "root_configs": ("README.md", "requirements.txt", "pyproject.toml", "docker-compose.yml", "compose.yaml", "Dockerfile", ".gitignore"),
}


def safe_backup_directory(path: Path) -> Path:
    """Validate again at execution time; never trust persisted target settings."""
    root = ROOT.resolve()
    base = root / "backups"
    candidate = Path(os.path.abspath(path))
    if not candidate.is_relative_to(base):
        raise ValueError("Backup target must remain under project backups/")
    for part in (base, *candidate.parents, candidate):
        if part.is_relative_to(base) and part.is_symlink():
            raise ValueError("Linked backup directory refused")
    if not candidate.resolve().is_relative_to(base):
        raise ValueError("Backup path escapes root")
    return candidate


def prune(paths: Iterable[Path], retention: int) -> None:
    items = sorted((path for path in paths if path.exists()), key=lambda p: p.stat().st_mtime, reverse=True)
    for item in items[max(0, retention):]: item.unlink(missing_ok=True)


def retain_local_archive(source: Path, retention: int, destination_dir: Path | None = None) -> Path:
    if isinstance(retention, bool) or int(retention) < 1:
        raise ValueError("At least one local archive must be retained")
    destination_dir = safe_backup_directory(destination_dir or LOCAL_DIR)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if destination.is_symlink() or (destination.exists() and destination.stat().st_nlink > 1):
        raise ValueError("Linked backup file refused")
    # Atomic replacement never truncates an existing hard-linked file.
    fd, temporary = tempfile.mkstemp(prefix=".archive-", dir=destination_dir)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    # Jobs sharing a target directory must not delete one another's history.
    archive_name = re.compile(r"okxquant_backup_(.+)_\d{8}_\d{6}(?:_\d{6})?\.tar\.gz(?:\.aes256)?$")
    identity = archive_name.fullmatch(source.name)
    if identity:
        siblings = (p for p in destination_dir.glob("okxquant_backup_*")
                    if (match := archive_name.fullmatch(p.name)) and match.group(1) == identity.group(1))
        prune(siblings, retention)
    return destination


def sqlite_hot_backups(timestamp: str, retention: int, destination_dir: Path | None = None) -> list[Path]:
    destination_dir = safe_backup_directory(destination_dir or SQLITE_DIR)
    destination_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for source in (ROOT / "data").glob("*.db"):
        if source.is_symlink() or source.stat().st_nlink != 1 or _excluded("data/" + source.name, []): continue
        destination = destination_dir / f"{source.stem}_{timestamp}.db"
        if destination.exists() or destination.is_symlink():
            raise ValueError("SQLite backup destination already exists")
        source_conn = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            target_conn = sqlite3.connect(destination)
            try: source_conn.backup(target_conn)
            finally: target_conn.close()
        finally: source_conn.close()
        os.chmod(destination, 0o600); created.append(destination)
    for source in (ROOT / "data").glob("*.db"):
        prune(destination_dir.glob(f"{source.stem}_*.db"), retention)
    return created


def calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


def _excluded(relative: str, patterns: list[str]) -> bool:
    rel = relative.replace(os.sep, "/")
    while rel.startswith("./"):
        rel = rel[2:]
    parts = tuple(rel.split("/"))
    from scripts.backup_restore import PRIVATE_PARTS, PRIVATE_SUFFIXES, _allowed
    if any(p.casefold().startswith(".env") or p.casefold() in PRIVATE_PARTS for p in parts):
        return True
    if parts[-1].casefold().endswith(PRIVATE_SUFFIXES):
        return True
    if parts[0] == "data" and not _allowed(parts, True):
        return True
    return any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f"{rel}/", pattern) for pattern in [*MANDATORY_EXCLUDES, *patterns])


def _tar_filter(patterns: list[str]):
    def filter_info(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        return None if not (info.isfile() or info.isdir()) or _excluded(info.name, patterns) else info
    return filter_info


def create_archive(job: dict[str, Any], timestamp: str) -> tuple[Path, list[str]]:
    staging = safe_backup_directory(BACKUPS / "staging"); staging.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c for c in str(job["id"]) if c.isalnum() or c in "-_")[:48]
    path = staging / f"okxquant_backup_{safe_id}_{timestamp}.tar.gz"
    included: list[str] = []
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz", compresslevel=int(job.get("compression_level", 6))) as archive:
        patterns = job.get("exclude", [])
        def add_source(source: Path, relative: str) -> None:
            if source.is_symlink() or _excluded(relative, patterns):
                return
            if source.is_dir():
                archive.add(source, arcname=relative, recursive=False, filter=_tar_filter(patterns))
                for child in sorted(source.iterdir()):
                    add_source(child, relative + "/" + child.name)
            elif source.is_file() and source.stat().st_nlink == 1:
                if relative.startswith("data/") and source.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                    # The archive itself must be WAL-consistent even when the
                    # separate optional SQLite-retention target is disabled.
                    with tempfile.TemporaryDirectory(prefix=".sqlite-archive-", dir=staging) as directory:
                        snapshot = Path(directory) / "snapshot.db"
                        snapshot.touch(mode=0o600)
                        reader = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
                        try:
                            writer = sqlite3.connect(snapshot)
                            try:
                                reader.backup(writer)
                            finally:
                                writer.close()
                        finally:
                            reader.close()
                        archive.add(snapshot, arcname=relative, recursive=False, filter=_tar_filter(patterns))
                else:
                    archive.add(source, arcname=relative, recursive=False, filter=_tar_filter(patterns))
        for scope in job.get("scope", []):
            for relative in SCOPE_PATHS.get(scope, ()):
                source = ROOT / relative
                if source.exists() and not _excluded(relative, job.get("exclude", [])):
                    add_source(source, relative); included.append(relative)
    if not included:
        path.unlink(missing_ok=True); raise RuntimeError("所选范围没有可归档文件")
    os.chmod(path, 0o600)
    return path, included


def _derive_key(secret: str, salt: bytes) -> bytes:
    if len(secret) < 16: raise RuntimeError("备份加密密钥至少 16 个字符")
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=32 * 1024 * 1024)


def encrypt_archive(source: Path, key_env: str) -> Path:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    secret = os.getenv(key_env, "")
    if not secret: raise RuntimeError(f"加密已启用但环境变量 {key_env} 未配置")
    salt = os.urandom(16); nonce = os.urandom(12); key = _derive_key(secret, salt); target = source.with_suffix(source.suffix + ".aes256")
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb+") as out, source.open("rb") as inp:
        out.write(MAGIC); out.write(salt); out.write(nonce); out.write(b"\x00" * 16)
        for chunk in iter(lambda: inp.read(1024 * 1024), b""): out.write(encryptor.update(chunk))
        out.write(encryptor.finalize()); out.seek(len(MAGIC) + len(salt) + len(nonce)); out.write(encryptor.tag)
        out.flush(); os.fsync(out.fileno())
    os.chmod(target, 0o600); source.unlink(missing_ok=True); return target


def decrypt_archive(source: Path, key_env: str, destination: Path) -> Path:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    secret = os.getenv(key_env, "")
    if not secret: raise RuntimeError(f"解密需要环境变量 {key_env}")
    with source.open("rb") as inp:
        if inp.read(len(MAGIC)) != MAGIC: raise RuntimeError("不是受支持的 OKXQuant AES-256-GCM 归档")
        salt, nonce, tag = inp.read(16), inp.read(12), inp.read(16)
        if len(salt) != 16 or len(nonce) != 12 or len(tag) != 16: raise RuntimeError("加密归档头损坏")
        decryptor = Cipher(algorithms.AES(_derive_key(secret, salt)), modes.GCM(nonce, tag)).decryptor()
        if destination.is_symlink() or (destination.exists() and destination.stat().st_nlink != 1):
            raise ValueError("Linked decryption destination refused")
        fd, temporary = tempfile.mkstemp(prefix=".decrypt-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in iter(lambda: inp.read(1024 * 1024), b""):
                    out.write(decryptor.update(chunk))
                # Publish only after authentication succeeds; a bad tag must
                # neither truncate the previous file nor expose partial plaintext.
                out.write(decryptor.finalize()); out.flush(); os.fsync(out.fileno())
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return destination


def verify_archive(path: Path, expected_sha256: str = "", key_env: str = "") -> dict[str, Any]:
    safe_backup_directory(BACKUPS).mkdir(parents=True, exist_ok=True)
    if not path.exists(): raise RuntimeError("归档文件不存在")
    from scripts.backup_restore import RestoreLimits, restore_archive
    if path.is_symlink() or path.stat().st_nlink != 1 or not path.is_file():
        raise ValueError("Archive must be an independent regular file")
    if path.stat().st_size > RestoreLimits().max_archive_bytes:
        raise ValueError("Archive exceeds verification size limit")
    checksum = calculate_sha256(path)
    if expected_sha256 and checksum != expected_sha256: raise RuntimeError("SHA256 校验失败")
    temp: Path | None = None; tar_path = path
    try:
        if path.name.endswith(".aes256"):
            fd, temp_name = tempfile.mkstemp(prefix="okxquant-verify-", suffix=".tar.gz", dir=BACKUPS)
            os.close(fd); temp = Path(temp_name); tar_path = decrypt_archive(path, key_env, temp)
        # Verification and restore share the same CRC, decompression, metadata,
        # member-count, link, path and alias limits. Never call getmembers() on
        # an unbounded archive supplied to the administrative verify endpoint.
        with tempfile.TemporaryDirectory(prefix="okxquant-verify-target-") as target:
            checked = restore_archive(tar_path, Path(target), verify_only=True)
        return {"valid": True, "sha256": checksum, **checked, "encrypted": path.name.endswith(".aes256")}

    finally:
        if temp: temp.unlink(missing_ok=True)


def upload_baidu(source: Path, remote_path: str, retries: int) -> dict[str, Any]:
    import bypy
    remote = "/".join(part for part in Path(remote_path).parts if part not in {"/", "."})
    destination = f"{remote}/{source.name}" if remote else source.name
    last = ""
    for attempt in range(1, retries + 1):
        try:
            code = bypy.ByPy().upload(str(source), destination)
            if code == 0: return {"success": True, "attempts": attempt, "destination": f"/apps/bypy/{destination}"}
            last = f"ByPy 返回 {code}"
        except Exception as exc: last = f"{type(exc).__name__}: backup operation failed; external detail withheld"
        if attempt < retries: time.sleep(min(attempt * 5, 30))
    return {"success": False, "attempts": retries, "destination": destination, "error": last}


def _credentials(target: dict[str, Any]) -> dict[str, str]:
    from okxquant_backend.backup_secrets import load_credentials
    return load_credentials(str(target.get("credential_ref") or f"backup:{target['id']}"))


def _urlencoded_json(url: str, data: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, headers={"User-Agent": "OKXQuant-Backup/6.2.0"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response: raw = response.read().decode("utf-8")
    payload = json.loads(raw or "{}")
    if payload.get("errno") not in (None, 0) or payload.get("error"):
        raise RuntimeError(str(payload.get("errmsg") or payload.get("error_description") or payload))
    return payload


def _multipart_upload(url: str, field_name: str, filename: str, content: bytes, timeout: int = 180) -> dict[str, Any]:
    boundary = f"----OKXQuant{hashlib.sha256(os.urandom(16)).hexdigest()[:24]}"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field_name}\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "OKXQuant-Backup/6.2.0"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response: payload = json.loads(response.read().decode("utf-8") or "{}")
    if payload.get("errno") not in (None, 0): raise RuntimeError(str(payload.get("errmsg") or payload))
    return payload


def upload_baidu_oauth(source: Path, target: dict[str, Any]) -> dict[str, Any]:
    from okxquant_backend.backup_secrets import save_credentials
    creds = _credentials(target); app_key = creds.get("app_key", ""); app_secret = creds.get("app_secret", ""); refresh_token = creds.get("refresh_token", "")
    if not app_key or not app_secret or not refresh_token: raise RuntimeError("百度官方 OAuth 需要 App Key、App Secret 与 Refresh Token")
    token_url = "https://openapi.baidu.com/oauth/2.0/token?" + urllib.parse.urlencode({"grant_type":"refresh_token","refresh_token":refresh_token,"client_id":app_key,"client_secret":app_secret})
    token = _urlencoded_json(token_url); access_token = str(token.get("access_token") or "")
    if not access_token: raise RuntimeError("百度 OAuth 未返回 Access Token")
    if token.get("refresh_token") and token["refresh_token"] != refresh_token:
        save_credentials(str(target.get("credential_ref")), {"refresh_token": token["refresh_token"]})
    chunk_size = 4 * 1024 * 1024; block_list: list[str] = []
    with source.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk: break
            block_list.append(hashlib.md5(chunk).hexdigest())
    remote_dir = str(target.get("remote_path") or "OKXQUANT_Backups").strip("/")
    remote_path = f"/apps/OKXQuant/{remote_dir}/{source.name}" if remote_dir else f"/apps/OKXQuant/{source.name}"
    precreate_url = "https://pan.baidu.com/rest/2.0/xpan/file?method=precreate&access_token=" + urllib.parse.quote(access_token, safe="")
    common = {"path":remote_path,"size":source.stat().st_size,"isdir":0,"autoinit":1,"rtype":3,"block_list":json.dumps(block_list)}
    precreated = _urlencoded_json(precreate_url, common); upload_id = str(precreated.get("uploadid") or "")
    if not upload_id: raise RuntimeError("百度网盘预创建未返回 uploadid")
    with source.open("rb") as handle:
        for part_seq in range(len(block_list)):
            chunk = handle.read(chunk_size)
            upload_url = "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2?" + urllib.parse.urlencode({"method":"upload","type":"tmpfile","access_token":access_token,"path":remote_path,"uploadid":upload_id,"partseq":part_seq})
            uploaded = _multipart_upload(upload_url, "file", source.name, chunk)
            if uploaded.get("md5") and uploaded["md5"] != block_list[part_seq]: raise RuntimeError(f"百度分片 {part_seq} MD5 校验失败")
    create_url = "https://pan.baidu.com/rest/2.0/xpan/file?method=create&access_token=" + urllib.parse.quote(access_token, safe="")
    created = _urlencoded_json(create_url, {**common,"uploadid":upload_id})
    return {"success":True,"attempts":1,"destination":remote_path,"fs_id":str(created.get("fs_id") or "")}


def upload_s3(source: Path, target: dict[str, Any]) -> dict[str, Any]:
    """S3-compatible SigV4 upload without a mandatory boto3 dependency."""
    import hmac
    creds = _credentials(target); access = creds.get("access_key_id", ""); secret = creds.get("secret_access_key", "")
    if not access or not secret: raise RuntimeError("S3 Access Key / Secret Key 未配置")
    endpoint = str(target["endpoint"]).rstrip("/"); bucket = str(target["bucket"]); region = str(target.get("region") or "us-east-1")
    key = "/".join(x for x in [str(target.get("remote_path") or "").strip("/"), source.name] if x)
    parsed = urllib.parse.urlparse(endpoint); host = parsed.netloc
    path = f"/{bucket}/{urllib.parse.quote(key, safe='/')}" if target.get("force_path_style") else f"/{urllib.parse.quote(key, safe='/')}"
    if not target.get("force_path_style"): host = f"{bucket}.{host}"
    now = datetime.now(timezone.utc); amzdate = now.strftime("%Y%m%dT%H%M%SZ"); datestamp = now.strftime("%Y%m%d")
    payload_hash = calculate_sha256(source)
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amzdate, "content-length": str(source.stat().st_size)}
    if creds.get("session_token"): headers["x-amz-security-token"] = creds["session_token"]
    signed_headers = ";".join(sorted(headers)); canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical = f"PUT\n{path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    scope = f"{datestamp}/{region}/s3/aws4_request"; string_to_sign = f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    sign=lambda key,msg:hmac.new(key,msg.encode(),hashlib.sha256).digest()
    signing=sign(sign(sign(sign(("AWS4"+secret).encode(),datestamp),region),"s3"),"aws4_request")
    headers["authorization"] = f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed_headers}, Signature={hmac.new(signing,string_to_sign.encode(),hashlib.sha256).hexdigest()}"
    url=f"{parsed.scheme}://{host}{path}"
    import http.client
    connection = (http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection)(host, timeout=300)
    connection.putrequest("PUT", path, skip_host=True); [connection.putheader(k, v) for k, v in headers.items()]; connection.endheaders()
    with source.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk: break
            connection.send(chunk)
    response = connection.getresponse(); response.read(); connection.close()
    if response.status not in (200,201): raise RuntimeError(f"S3 HTTP {response.status}")
    return {"success":True,"attempts":1,"destination":f"s3://{bucket}/{key}"}


def upload_oss(source: Path, target: dict[str, Any]) -> dict[str, Any]:
    import oss2
    creds=_credentials(target); access=creds.get("access_key_id",""); secret=creds.get("secret_access_key","")
    if not access or not secret: raise RuntimeError("OSS AccessKey ID / Secret 未配置")
    key="/".join(x for x in [str(target.get("remote_path") or "").strip("/"),source.name] if x)
    bucket=oss2.Bucket(oss2.Auth(access,secret),str(target["endpoint"]),str(target["bucket"])); bucket.put_object_from_file(key,str(source))
    return {"success":True,"attempts":1,"destination":f"oss://{target['bucket']}/{key}"}


def upload_webdav(source: Path, target: dict[str, Any]) -> dict[str, Any]:
    creds=_credentials(target); endpoint=str(target["endpoint"]).rstrip("/"); remote=str(target.get("remote_path") or "").strip("/")
    auth=""; username=creds.get("username",""); password=creds.get("password","")
    if username: auth="Basic "+base64.b64encode(f"{username}:{password}".encode()).decode()
    current=endpoint
    for part in [x for x in remote.split("/") if x]:
        current += "/"+urllib.parse.quote(part,safe="")
        req=urllib.request.Request(current,headers={"Authorization":auth} if auth else {},method="MKCOL")
        try: urllib.request.urlopen(req,timeout=20).close()
        except urllib.error.HTTPError as exc:
            if exc.code not in (301,302,405): raise
    url=current+"/"+urllib.parse.quote(source.name,safe=""); parsed=urllib.parse.urlparse(url)
    import http.client
    connection=(http.client.HTTPSConnection if parsed.scheme=="https" else http.client.HTTPConnection)(parsed.netloc,timeout=300)
    path=parsed.path+(f"?{parsed.query}" if parsed.query else ""); connection.putrequest("PUT",path)
    connection.putheader("Content-Type","application/octet-stream"); connection.putheader("Content-Length",str(source.stat().st_size))
    if auth: connection.putheader("Authorization",auth)
    connection.endheaders()
    with source.open("rb") as handle:
        while True:
            chunk=handle.read(1024*1024)
            if not chunk: break
            connection.send(chunk)
    response=connection.getresponse(); response.read(); connection.close()
    if response.status not in (200,201,204): raise RuntimeError(f"WebDAV HTTP {response.status}")
    return {"success":True,"attempts":1,"destination":url}


def deliver_target(source: Path, target: dict[str, Any]) -> dict[str, Any]:
    target_type=target["type"]
    if target_type in {"s3","oss","webdav","aliyundrive","quark"}:
        from okxquant_backend.net_security import validate_outbound_url
        target = {**target, "endpoint": validate_outbound_url(str(target.get("endpoint") or ""), allow_private=bool(target.get("allow_private_endpoint")))}
    if target_type=="baidu":
        if target.get("auth_mode","bypy")=="oauth": return upload_baidu_oauth(source,target)
        return upload_baidu(source,target.get("remote_path","OKXQUANT_Backups"),int(target.get("retries",3)))
    if target_type=="local":
        destination=ROOT/str(target.get("path") or "backups/local")
        return {"success":True,"attempts":1,"destination":str(retain_local_archive(source,int(target.get("retention",3)),destination).relative_to(ROOT))}
    upload = upload_s3 if target_type == "s3" else upload_oss if target_type == "oss" else upload_webdav if target_type in {"webdav","aliyundrive","quark"} else None
    if not upload: raise RuntimeError(f"不支持的灾备目标：{target_type}")
    last = ""; retries = int(target.get("retries", 3))
    for attempt in range(1, retries + 1):
        try:
            result = upload(source, target); result["attempts"] = attempt; return result
        except Exception as exc:
            last = f"{type(exc).__name__}: backup operation failed; external detail withheld"
            if attempt < retries: time.sleep(min(attempt * 5, 30))
    return {"success": False, "attempts": retries, "error": last}


def run_backup_job(job: dict[str, Any]) -> dict[str, Any]:
    from okxquant_backend.backup_store import validate_job_id
    validate_job_id(str(job["id"]))
    safe_backup_directory(MANIFEST_DIR)
    started = datetime.now(BJ_TZ); stamp = started.strftime("%Y%m%d_%H%M%S_%f")
    result: dict[str, Any] = {"job_id": job["id"], "job_name": job["name"], "started_at": started.strftime("%Y-%m-%d %H:%M:%S"), "status": "running", "targets": [], "sqlite": [], "errors": []}
    archive: Path | None = None
    try:
        enabled_file_targets = [x for x in job.get("targets", []) if x.get("enabled")]
        if enabled_file_targets:
            if job.get("pre_backup_sync"):
                script = ROOT / "scripts" / "sync_full_ledger.py"
                if script.exists(): subprocess.run([sys.executable, str(script)], cwd=ROOT, timeout=60, check=False, capture_output=True, text=True)
            archive, included = create_archive(job, stamp); result["included"] = included
            if job.get("encryption", {}).get("enabled"):
                archive = encrypt_archive(archive, str(job["encryption"]["key_env"])); result["encrypted"] = True
            else: result["encrypted"] = False
            result["archive"] = archive.name; result["bytes"] = archive.stat().st_size; result["sha256"] = calculate_sha256(archive)
            verification = verify_archive(archive, result["sha256"], str(job.get("encryption", {}).get("key_env", "")))
            result["archive_members"] = verification["members"]; result["archive_roots"] = verification["roots"]
            for target in enabled_file_targets:
                try: target_result = deliver_target(archive, target)
                except Exception as exc: target_result = {"success": False, "attempts": 1, "error": f"{type(exc).__name__}: backup operation failed; external detail withheld"}
                result["targets"].append({"id": target["id"], "type": target["type"], **target_result})
        if job.get("sqlite", {}).get("enabled"):
            sqlite_dir = SQLITE_DIR / str(job["id"])
            result["sqlite"] = [str(x.relative_to(ROOT)) for x in sqlite_hot_backups(stamp, int(job["sqlite"]["retention"]), sqlite_dir)]
        target_success = [x for x in result["targets"] if x.get("success")]
        target_failure = [x for x in result["targets"] if not x.get("success")]
        any_success = bool(target_success or result["sqlite"])
        all_file_targets_succeeded = bool(enabled_file_targets) and not target_failure and len(target_success) == len(enabled_file_targets)
        result["status"] = "success" if any_success and not target_failure else "partial" if any_success else "failed"
        if target_failure: result["errors"].extend(str(x.get("error") or "目标失败") for x in target_failure)
        if archive and job.get("cleanup_local_on_success") and all_file_targets_succeeded:
            archive.unlink(missing_ok=True); result["temporary_cleaned"] = True
        elif archive: result["temporary_cleaned"] = False
    except Exception as exc:
        result["status"] = "failed"; result["errors"].append(f"{type(exc).__name__}: backup operation failed; external detail withheld")
    result["finished_at"] = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    manifest = MANIFEST_DIR / f"{job['id']}_{stamp}.json"
    fd = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    result["manifest"] = str(manifest.relative_to(ROOT))
    return result
