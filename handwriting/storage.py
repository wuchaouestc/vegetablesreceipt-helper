"""Versioned, local-only artifacts; no production cache or settings mutations."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def data_root():
    import 路径
    return 路径.程序目录() / "data"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ResponseCache:
    """Cache successful responses only; caller supplies complete request identity."""
    def __init__(self, root):
        self.root = Path(root)

    def get(self, identity):
        try:
            entry = read_json(self.root / (fingerprint(identity) + ".json"))
            if (entry.get("identity") == identity and entry.get("cache_version") == 1
                    and entry.get("response", {}).get("status") in ("ok", "empty")):
                return entry["response"]
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return None

    def put(self, identity, response):
        if response.get("status") in ("ok", "empty"):
            write_json(self.root / (fingerprint(identity) + ".json"),
                       {"cache_version": 1, "identity": identity, "response": response})
