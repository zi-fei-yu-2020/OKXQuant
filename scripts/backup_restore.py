"""Offline, bounded restore of *trusted* OKXQuant source/runtime archives.

No service/config imports, network, process management, or tar.extract. Source
scope matches backup_runtime.SCOPE_PATHS; this is not a signature verifier or a
sandbox for untrusted Python code. Supply only administrator-trusted archives.
Secret/auth material and transient files are skipped (and reported). All headers,
paths, destination objects and payloads are checked before any destination write.
Publication is atomic per file, not a whole-tree transaction on disk failure.
Linux/POSIX only: descriptor-relative, no-follow traversal prevents link races.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import gzip
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile
import unicodedata


class UnsafeArchive(ValueError):
    """Archive or destination violates the restore policy."""


@dataclass(frozen=True)
class RestoreLimits:
    max_file_bytes: int = 512 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024
    max_archive_bytes: int = 2 * 1024 * 1024 * 1024
    max_members: int = 20_000
    max_metadata_bytes: int = 64 * 1024 * 1024
    max_path_bytes: int = 1024
    max_depth: int = 32


# Deliberately kept independent of backup_runtime (no live configuration imports).
SOURCE_DIRS = frozenset({"scripts", "dashboard", "okxquant_backend", "okxquant_gateway", "tests"})
ROOT_FILES = frozenset({
    "RECOVERY_GUIDE.md", "SOUL.md", "PROFILE.md", "AGENTS.md", "MEMORY.md",
    "README.md", "requirements.txt", "pyproject.toml", "docker-compose.yml", "compose.yaml",
    "Dockerfile", ".gitignore",
})
SOURCE_SUFFIXES = frozenset({".py", ".pyi", ".sh", ".ps1", ".bat", ".md", ".rst", ".txt",
    ".json", ".jsonl", ".toml", ".ini", ".cfg", ".yaml", ".yml", ".html", ".css",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svg", ".png", ".jpg",
    ".jpeg", ".ico", ".webp", ".woff", ".woff2", ".ttf"})
DATA_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3", ".json", ".jsonl", ".md", ".csv", ".txt"})
PRIVATE_PARTS = frozenset({".git", ".okx", ".bypy", ".ssh", ".aws", ".azure", "credentials", "credential", "secrets", "secret", "auth", "authentication", "tokens", "keys", "node_modules", "__pycache__", ".venv", "venv"})
PRIVATE_SUFFIXES = (".enc", ".pem", ".key", ".p12", ".pfx", ".keystore", ".pyc")
# These JSON files contain inline provider credentials in legacy/current formats.
PRIVATE_DATA_PREFIXES = ("llm_models", "llm_providers", "llm_config", "settings.", "config.")
WINDOWS_DEVICES = frozenset({"con", "prn", "aux", "nul", "clock$", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))})


def _parts(member: tarfile.TarInfo, limits: RestoreLimits) -> tuple[str, ...]:
    name = member.name
    if (not name or len(name.encode("utf-8")) > limits.max_path_bytes
            or name.startswith("/") or "\\" in name or ":" in name
            or any(c in '<>"|?*' for c in name)
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise UnsafeArchive("非法归档路径（绝对路径、Windows 路径或控制字符）")
    parts = tuple(name.rstrip("/").split("/"))
    if len(parts) > limits.max_depth or any(
        p in {"", ".", ".."} or p.endswith((".", " "))
        or p.split(".")[0].casefold() in WINDOWS_DEVICES
        for p in parts
    ):
        raise UnsafeArchive("非法归档路径组件")
    return parts


def _allowed(parts: tuple[str, ...], directory: bool) -> bool:
    lower = tuple(p.casefold() for p in parts)
    if any(p in PRIVATE_PARTS or p.startswith(".env") for p in lower):
        return False
    if (lower[-1].endswith(PRIVATE_SUFFIXES) or lower[-1].startswith(PRIVATE_DATA_PREFIXES)
            or lower[-1].startswith("okxquant_admin.db")):
        return False
    if parts[0] in SOURCE_DIRS:
        # Code implementing auth/secrets is source, NOT a stored credential.
        # Non-code credential/key exports are never a supported source asset.
        if not directory and not lower[-1].endswith((".py", ".ts", ".js", ".mjs", ".vue")):
            if any(word in lower[-1] for word in ("auth", "credential", "secret", "password", "token", "session", "_key")):
                return False
        return directory or Path(lower[-1]).suffix in SOURCE_SUFFIXES
    if parts[0] == "data":
        if any(p.startswith(".") or any(word in p for word in
               ("auth", "admin", "secret", "credential", "password", "token", "session", "key"))
               for p in lower[1:]):
            return False
        if lower[-1].endswith((".pid", ".lock", ".db-wal", ".db-shm", ".db-journal")):
            return False
        return directory or Path(lower[-1]).suffix in DATA_SUFFIXES
    return len(parts) == 1 and parts[0] in ROOT_FILES and not directory


def _check_object(info: os.stat_result, directory: bool) -> None:
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise UnsafeArchive("目标父目录不是普通目录，拒绝链接或类型冲突")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise UnsafeArchive("已有目标不是独立普通文件，拒绝链接或类型冲突")


@contextmanager
def _root_fd(destination: Path):
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise UnsafeArchive("安全恢复仅支持 POSIX/Linux；请使用离线 Linux 恢复环境")
    # Do not resolve(): resolving would hide a pre-existing symlink ancestor.
    root = Path(os.path.abspath(destination))
    fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in root.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    except OSError as exc:
        raise UnsafeArchive("恢复根目录不存在、不安全或无法访问") from exc
    finally:
        os.close(fd)


def _preflight(root_fd: int, parts: tuple[str, ...], directory: bool, *, check_sqlite: bool = False) -> None:
    fd = os.dup(root_fd)
    try:
        for i, part in enumerate(parts):
            if check_sqlite and i == len(parts) - 1 and not directory and Path(part).suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                # Old SQLite WAL/journals could replay over the restored database.
                # Never delete them automatically; require an offline clean shutdown.
                for suffix in ("-wal", "-shm", "-journal"):
                    try:
                        os.stat(part + suffix, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    raise UnsafeArchive("目标数据库仍有 WAL/SHM/journal；请先完成离线干净停机检查")
            try:
                info = os.stat(part, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            is_dir = i < len(parts) - 1 or directory
            _check_object(info, is_dir)
            if is_dir:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
    finally:
        os.close(fd)


@contextmanager
def _directory_fd(root_fd: int, parts: tuple[str, ...]):
    fd = os.dup(root_fd)
    try:
        for part in parts:
            try:
                os.mkdir(part, mode=0o755, dir_fd=fd)
            except FileExistsError:
                pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def _check_headers(path: Path, limits: RestoreLimits) -> None:
    """Bound raw headers before tarfile allocates PAX/GNU extension payloads."""
    metadata = count = extension_chain = 0
    extended_types = {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK}
    length = path.stat().st_size
    with path.open("rb") as source:
        while True:
            block = source.read(tarfile.BLOCKSIZE)
            if not block or len(block) != tarfile.BLOCKSIZE:
                raise UnsafeArchive("归档缺少完整结束标记")
            if block == b"\0" * tarfile.BLOCKSIZE:
                if source.read(tarfile.BLOCKSIZE) != b"\0" * tarfile.BLOCKSIZE:
                    raise UnsafeArchive("归档结束标记不完整")
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    if any(chunk):
                        raise UnsafeArchive("归档结束标记后含未验证内容")
                return
            info = tarfile.TarInfo.frombuf(block, "utf-8", "surrogateescape")
            count += 1
            if count > limits.max_members:
                raise UnsafeArchive("归档头数量超过限额")
            if info.size < 0:
                raise UnsafeArchive("归档头大小非法")
            if info.type in extended_types:
                extension_chain += 1
                if extension_chain > limits.max_depth:
                    raise UnsafeArchive("连续归档扩展头超过限额")
                metadata += info.size
                if metadata > limits.max_metadata_bytes:
                    raise UnsafeArchive("归档扩展元数据超过限额")
            else:
                extension_chain = 0
                if info.size > limits.max_file_bytes:
                    raise UnsafeArchive("归档成员超过单文件限额")
            end = source.tell() + (info.size + 511) // 512 * 512
            if end > length:
                raise UnsafeArchive("归档文件内容截断")
            source.seek(end)


def restore_archive(archive: Path, destination: Path, *, limits: RestoreLimits | None = None, verify_only: bool = False) -> dict:
    """Validate and stage the whole archive, then replace allowed regular files.

    Existing ordinary files may be replaced; symlinks/hardlinks/special objects
    may not. destination must already exist. verify_only checks the same bounded
    headers/payload stream without publishing any member. Excluded members are still checked
    for unsafe types, paths, duplicate conflicts and size limits.
    """
    limits = limits or RestoreLimits()
    if any(value <= 0 for value in vars(limits).values()):
        raise ValueError("恢复限额必须为正数")
    restored, skipped = [], []
    try:
        with _root_fd(Path(archive).parent) as archive_dir, _root_fd(Path(destination)) as root_fd, tempfile.TemporaryDirectory(prefix="okxquant-restore-") as temporary:
            stage = Path(temporary)
            archive_fd = os.open(Path(archive).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=archive_dir)
            with os.fdopen(archive_fd, "rb") as source:
                info = os.fstat(source.fileno())
                _check_object(info, False)
                if info.st_size > limits.max_archive_bytes:
                    raise UnsafeArchive("压缩归档超过大小限额")
                # Read gzip to EOF, including CRC/trailer, before any target write.
                # This also caps decompression bombs and oversized PAX headers.
                expanded = 0
                with gzip.GzipFile(fileobj=source) as compressed, (stage / "archive.tar").open("wb") as raw:
                    while True:
                        chunk = compressed.read(1024 * 1024)
                        if not chunk:
                            break
                        expanded += len(chunk)
                        if expanded > limits.max_total_bytes + limits.max_metadata_bytes:
                            raise UnsafeArchive("归档展开大小超过限额")
                        raw.write(chunk)
            _check_headers(stage / "archive.tar", limits)
            entries, seen, parents, spellings = [], {}, set(), {}
            total = 0
            with tarfile.open(stage / "archive.tar", "r:") as tar:
                for member in tar:
                    if len(entries) >= limits.max_members:
                        raise UnsafeArchive("归档成员数量超过限额")
                    parts = _parts(member, limits)
                    if (not (member.isdir() or member.isreg()) or member.issparse()
                            or member.linkname or any(k.startswith("GNU.sparse") for k in member.pax_headers)):
                        raise UnsafeArchive("归档含链接、设备、FIFO、稀疏文件或非普通成员")
                    if member.size < 0 or member.size > limits.max_file_bytes or (member.isdir() and member.size):
                        raise UnsafeArchive("归档成员大小非法或超过单文件限额")
                    total += member.size
                    if total > limits.max_total_bytes:
                        raise UnsafeArchive("归档文件总大小超过限额")
                    # Case/Unicode aliases are also conflicts on portable backups.
                    key = tuple(unicodedata.normalize("NFC", p).casefold() for p in parts)
                    for i in range(1, len(key) + 1):
                        prefix = key[:i]
                        if prefix in spellings and spellings[prefix] != parts[:i]:
                            raise UnsafeArchive("归档路径含大小写或 Unicode 别名冲突")
                        spellings[prefix] = parts[:i]
                    if key in seen or (not member.isdir() and key in parents):
                        raise UnsafeArchive("归档含重复路径或文件/目录冲突")
                    for i in range(1, len(key)):
                        parent = key[:i]
                        if parent in seen and not seen[parent]:
                            raise UnsafeArchive("归档文件与子路径冲突")
                        parents.add(parent)
                    seen[key] = member.isdir()
                    allowed = _allowed(parts, member.isdir())
                    if parts[0] not in SOURCE_DIRS | {"data"} and parts[0] not in ROOT_FILES:
                        # Known sensitive top-level entries are skipped, not restored.
                        if parts[0].casefold() not in PRIVATE_PARTS and not parts[0].casefold().startswith(".env"):
                            raise UnsafeArchive("归档路径不在现有 OKXQuant 备份恢复范围内")
                    # Even excluded entries cannot exploit an existing target link.
                    _preflight(root_fd, parts, member.isdir(), check_sqlite=allowed)
                    entries.append((member, parts, allowed))
                if verify_only:
                    return {"members": len(entries), "roots": sorted({parts[0] for _, parts, _ in entries})}
                for index, (member, parts, allowed) in enumerate(entries):
                    if not allowed:
                        skipped.append("/".join(parts))
                        continue
                    if member.isfile():
                        stream = tar.extractfile(member)
                        if stream is None:
                            raise UnsafeArchive("归档文件内容缺失")
                        with stream, (stage / str(index)).open("wb") as output:
                            shutil.copyfileobj(stream, output, 1024 * 1024)
                        if (stage / str(index)).stat().st_size != member.size:
                            raise UnsafeArchive("归档文件内容截断")
            # Re-check the entire target set immediately before publishing.
            for member, parts, allowed in entries:
                _preflight(root_fd, parts, member.isdir(), check_sqlite=allowed)
            for index, (member, parts, allowed) in enumerate(entries):
                if not allowed:
                    continue
                if member.isdir():
                    with _directory_fd(root_fd, parts):
                        pass
                    continue
                with _directory_fd(root_fd, parts[:-1]) as parent_fd:
                    _preflight(parent_fd, (parts[-1],), False, check_sqlite=True)
                    # Same-directory temp + rename: never open/truncate the old inode.
                    temporary_name = ".okxquant-restore-" + os.urandom(12).hex()
                    fd = os.open(temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
                    try:
                        with os.fdopen(fd, "wb") as output, (stage / str(index)).open("rb") as source:
                            shutil.copyfileobj(source, output, 1024 * 1024)
                            output.flush()
                            mode = 0o600 if parts[0] == "data" else 0o755 if member.mode & 0o111 else 0o644
                            os.fchmod(output.fileno(), mode)
                            os.fsync(output.fileno())
                        _preflight(parent_fd, (parts[-1],), False, check_sqlite=True)
                        os.replace(temporary_name, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                        restored.append("/".join(parts))
                    finally:
                        try:
                            os.unlink(temporary_name, dir_fd=parent_fd)
                        except FileNotFoundError:
                            pass
        return {"restored_count": len(restored), "sample_files": restored[:10],
                "skipped_count": len(skipped), "skipped_files": skipped[:50]}
    except UnsafeArchive:
        raise
    except (OSError, EOFError, tarfile.TarError, UnicodeError) as exc:
        raise UnsafeArchive("归档损坏或目标无法安全写入；若写入阶段磁盘故障，请保持服务停止并离线检查") from exc
