import time
from collections import OrderedDict

class Deduplicator:
    """
    Simple deduplicator using an OrderedDict as an LRU cache.
    Prevents double-processing of identical anketas within `ttl` seconds.
    """
    def __init__(self, maxsize=1000, ttl_seconds=600):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        self.ttl = ttl_seconds

    def _cleanup(self):
        now = time.time()
        # Remove expired
        keys_to_delete = []
        for key, timestamp in self.cache.items():
            if now - timestamp > self.ttl:
                keys_to_delete.append(key)
            else:
                break # OrderedDict means we hit newer ones
        for k in keys_to_delete:
            del self.cache[k]
            
        # Remove oldest if over maxsize
        while len(self.cache) > self.maxsize:
            self.cache.popitem(last=False)

    def is_duplicate(self, key: str) -> bool:
        """Returns True if the key was recently processed."""
        self._cleanup()
        if key in self.cache:
            return True
        self.cache[key] = time.time()
        return False

# Global instance for the telegram handler
anketa_deduplicator = Deduplicator(maxsize=1000, ttl_seconds=600)
