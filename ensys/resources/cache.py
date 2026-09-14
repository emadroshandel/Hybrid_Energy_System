"""
On-disk cache for provider responses.

Resource data for a fixed point does not change between runs, and several
providers are rate-limited (Renewables.ninja allows about 50 requests an
hour). Caching turns a study that would exhaust a quota into one that hits
the network once.

Entries are plain JSON files named by a hash of the key, with the key stored
inside so a cache directory stays inspectable by hand. There is no
expiry by default: long-term averages and reanalysis years are historical
facts and do not go stale. Pass `max_age_days` where that is not true.
"""

from __future__ import annotations

import hashlib
import json
import os
import time


class ResourceCache:
    """A directory of cached provider responses."""

    def __init__(self, directory, max_age_days=None):
        self.directory = directory
        self.max_age_s = (
            float(max_age_days) * 86400.0 if max_age_days else None
        )
        self._memory = {}
        self._available = True
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception:
            # A read-only or absent filesystem (Pyodide) is not an error:
            # fall back to an in-memory cache for the session.
            self._available = False

    def _path(self, key):
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return os.path.join(self.directory, f"{h}.json")

    def get(self, key):
        if key in self._memory:
            return self._memory[key]
        if not self._available:
            return None
        path = self._path(key)
        try:
            if not os.path.exists(path):
                return None
            if self.max_age_s is not None:
                age = time.time() - os.path.getmtime(path)
                if age > self.max_age_s:
                    return None
            with open(path, "r", encoding="utf-8") as f:
                record = json.load(f)
            if record.get("key") != key:
                return None            # hash collision, treat as a miss
            value = record.get("value")
            self._memory[key] = value
            return value
        except Exception:
            return None

    def put(self, key, value):
        self._memory[key] = value
        if not self._available:
            return False
        try:
            record = {"key": key, "stored_at": time.time(), "value": value}
            tmp = self._path(key) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f)
            os.replace(tmp, self._path(key))
            return True
        except Exception:
            # A failed cache write must never break a working fetch.
            return False

    def clear(self):
        self._memory.clear()
        if not self._available:
            return 0
        removed = 0
        try:
            for name in os.listdir(self.directory):
                if name.endswith(".json"):
                    os.remove(os.path.join(self.directory, name))
                    removed += 1
        except Exception:
            pass
        return removed

    def entries(self):
        """List what is cached, for the UI to show and let the user clear."""
        out = []
        if not self._available:
            return [{"key": k, "source": "memory"} for k in self._memory]
        try:
            for name in sorted(os.listdir(self.directory)):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(self.directory, name)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        record = json.load(f)
                    out.append(
                        {
                            "key": record.get("key"),
                            "stored_at": record.get("stored_at"),
                            "bytes": os.path.getsize(path),
                            "file": name,
                        }
                    )
                except Exception:
                    continue
        except Exception:
            pass
        return out
