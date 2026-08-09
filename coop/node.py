"""
Compute Co-op (L9) — distributed inference cache network.

Users who contribute idle compute get:
- Priority routing (faster response times)
- Discounted API access
- Co-op credits redeemable for premium features

Business model:
- Sell cached inference as "pre-computed responses" to third parties
- Users running the co-op node contribute GPU/CPU cycles
- Network effect: more nodes = faster cache = more users = more data

Legal foundation: Distributed computing cooperatives are well-established
(SETI@home, Folding@home, BOINC). This applies the same model to LLM inference.
"""

import json
import time
import hashlib
import threading
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from collections import OrderedDict


@dataclass
class CoOpConfig:
    enabled: bool = True
    port: int = 11435
    max_cache_entries: int = 10000
    cache_ttl_seconds: int = 3600  # 1 hour
    contributor_credit_rate: float = 1.0  # credits per cache hit served
    max_disk_cache_gb: int = 10
    anonymous_contributor_id: str = field(default_factory=lambda: hashlib.sha256(
        str(time.time()).encode()
    ).hexdigest()[:12])


class InferenceCache:
    """
    LRU cache for LLM responses.
    When a request matches a cached response (similar prompt + same model),
    serve it instantly without an API call.
    
    This is how CDNs work — cache popular content at the edge.
    Applied to LLM inference: cache popular queries at the user's machine.
    """
    
    def __init__(self, max_entries: int = 10000):
        self.max_entries = max_entries
        self.cache: OrderedDict = OrderedDict()
        self.lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.credits_earned = 0.0
        
        # Persistent cache on disk
        self.disk_cache_dir = Path.home() / ".freeai" / "cache"
        self.disk_cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _make_key(self, model: str, messages_hash: str, temperature: float = 0.0) -> str:
        return hashlib.sha256(
            f"{model}:{messages_hash}:{temperature}".encode()
        ).hexdigest()
    
    def _hash_messages(self, messages: List[Dict]) -> str:
        """Hash messages for cache lookup. Strips timestamps/IDs for better hit rate."""
        normalized = []
        for msg in messages:
            normalized.append({
                "role": msg.get("role", ""),
                "content": str(msg.get("content", ""))[:500],  # first 500 chars
            })
        return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    
    def lookup(
        self, model: str, messages: List[Dict], temperature: float = 0.0
    ) -> Optional[Dict]:
        """Check if response is cached."""
        key = self._make_key(model, self._hash_messages(messages), temperature)
        
        with self.lock:
            if key in self.cache:
                entry = self.cache[key]
                # Check TTL
                if time.time() - entry["ts"] < entry["ttl"]:
                    self.cache.move_to_end(key)
                    self.hits += 1
                    return entry["response"]
                else:
                    del self.cache[key]
        
        self.misses += 1
        
        # Check disk cache
        disk_key = key[:16]
        disk_path = self.disk_cache_dir / f"{disk_key}.json"
        if disk_path.exists():
            try:
                entry = json.loads(disk_path.read_text())
                if time.time() - entry["ts"] < entry["ttl"]:
                    return entry["response"]
            except (json.JSONDecodeError, KeyError):
                disk_path.unlink(missing_ok=True)
        
        return None
    
    def store(
        self, model: str, messages: List[Dict], response: Dict,
        temperature: float = 0.0, ttl: int = 3600
    ):
        """Cache a response."""
        key = self._make_key(model, self._hash_messages(messages), temperature)
        
        entry = {
            "model": model,
            "response": response,
            "ts": time.time(),
            "ttl": ttl,
        }
        
        with self.lock:
            self.cache[key] = entry
            self.cache.move_to_end(key)
            
            # Evict oldest if over capacity
            while len(self.cache) > self.max_entries:
                self.cache.popitem(last=False)
        
        # Also write to disk cache
        disk_key = key[:16]
        disk_path = self.disk_cache_dir / f"{disk_key}.json"
        try:
            disk_path.write_text(json.dumps(entry))
            self.credits_earned += 1.0  # 1 credit per cache entry stored
        except OSError:
            pass
    
    def get_stats(self) -> Dict:
        """Return cache statistics."""
        with self.lock:
            total = self.hits + self.misses
            return {
                "cache_size": len(self.cache),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / max(total, 1), 3),
                "credits_earned": round(self.credits_earned, 1),
                "disk_cache_bytes": sum(
                    f.stat().st_size for f in self.disk_cache_dir.glob("*.json")
                    if f.is_file()
                ),
            }


class CoOpNode:
    """
    Compute co-op node server.
    Runs alongside the main proxy on a separate port.
    Accepts cache lookup/store requests from other nodes on the local network.
    
    This implements a simple distributed cache:
    - Node A stores a response → available to Node B (if enabled)
    - Network effect: more users = more cache hits = faster responses
    """
    
    def __init__(self, config: CoOpConfig = None):
        self.config = config or CoOpConfig()
        self.cache = InferenceCache(max_entries=self.config.max_cache_entries)
        self.running = False
        self.contributors: Dict[str, Dict] = {}  # node_id → stats
    
    def register_contributor(self, node_id: str, node_info: Dict):
        """Register a contributing node."""
        self.contributors[node_id] = {
            "registered_at": time.time(),
            "last_seen": time.time(),
            "credits_contributed": 0.0,
            "cache_hits_served": 0,
            **node_info,
        }
    
    def record_contribution(self, node_id: str, credits: float = 1.0):
        """Record a cache contribution from a node."""
        if node_id in self.contributors:
            self.contributors[node_id]["credits_contributed"] += credits
            self.contributors[node_id]["cache_hits_served"] += 1
            self.contributors[node_id]["last_seen"] = time.time()
    
    def get_contributor_discount(self, node_id: str) -> float:
        """
        Contributors get discounted API routing.
        More contributions = higher discount (up to 100%).
        """
        if node_id not in self.contributors:
            return 0.0
        
        credits = self.contributors[node_id]["credits_contributed"]
        # Logarithmic scaling: 10 credits = 10%, 100 = 20%, 1000 = 30%
        discount = min(0.30, credits / 1000 * 0.30)
        return round(discount, 2)
    
    def get_network_stats(self) -> Dict:
        """Return co-op network statistics."""
        active_contributors = sum(
            1 for c in self.contributors.values()
            if time.time() - c["last_seen"] < 3600
        )
        total_credits = sum(
            c["credits_contributed"] for c in self.contributors.values()
        )
        total_hits = sum(
            c["cache_hits_served"] for c in self.contributors.values()
        )
        
        return {
            "active_contributors": active_contributors,
            "total_contributors": len(self.contributors),
            "total_credits_issued": round(total_credits, 1),
            "total_cache_hits_network": total_hits,
            "local_cache_stats": self.cache.get_stats(),
            "config": {
                "enabled": self.config.enabled,
                "port": self.config.port,
                "cache_ttl_hours": self.config.cache_ttl_seconds / 3600,
            },
        }


# ── Singleton ───────────────────────────────────────────────────

_COOP_NODE: Optional[CoOpNode] = None


def get_coop() -> CoOpNode:
    global _COOP_NODE
    if _COOP_NODE is None:
        _COOP_NODE = CoOpNode()
    return _COOP_NODE
