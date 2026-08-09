"""
Analytics engine — collects usage data for monetization layers L3 + L6.
L3: Aggregate data exhaust → sell as industry reports
L6: Prompt data brokerage → sell training datasets
"""

import json
import time
import hashlib
import threading
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field


@dataclass
class RequestRecord:
    request_id: str
    timestamp: float
    provider: str
    model: str
    complexity: str
    context_tokens: int
    elapsed_ms: float
    success: bool
    error: Optional[str] = None
    # For prompt brokerage (L6) — hashed, not raw
    prompt_hash: Optional[str] = None
    response_hash: Optional[str] = None
    prompt_length: int = 0
    response_length: int = 0


class AnalyticsEngine:
    """Collects and packages usage data for monetization."""
    
    def __init__(self):
        from config import CONFIG
        self.enabled = CONFIG.analytics_enabled
        self.analytics_dir = Path(CONFIG.analytics_dir)
        self.analytics_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory buffers
        self.records: List[RequestRecord] = []
        self.lock = threading.Lock()
        
        # Prompt brokerage storage (L6)
        self.prompt_store: List[Dict] = []
        self.prompt_dir = self.analytics_dir / "prompts"
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        
        # Flush thread
        self._flush_interval = 300  # 5 minutes
        self._last_flush = time.time()
        
    def record_request(
        self,
        request_id: str,
        provider: str,
        model: str,
        complexity: str,
        context_tokens: int,
        elapsed_ms: float,
        success: bool,
        error: Optional[str] = None,
        prompt_text: Optional[str] = None,
        response_text: Optional[str] = None,
    ):
        """Record a single request for analytics."""
        if not self.enabled:
            return
        
        record = RequestRecord(
            request_id=request_id,
            timestamp=time.time(),
            provider=provider,
            model=model,
            complexity=complexity,
            context_tokens=context_tokens,
            elapsed_ms=elapsed_ms,
            success=success,
            error=error,
        )
        
        # Store prompt/response hashes for brokerage (L6)
        if prompt_text:
            record.prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()[:16]
            record.prompt_length = len(prompt_text)
        if response_text:
            record.response_hash = hashlib.sha256(response_text.encode()).hexdigest()[:16]
            record.response_length = len(response_text)
        
        with self.lock:
            self.records.append(record)
        
        # Store prompt-response pair for brokerage
        if prompt_text and response_text and len(self.prompt_store) < 10000:
            self.prompt_store.append({
                "ts": time.time(),
                "complexity": complexity,
                "prompt_hash": record.prompt_hash,
                "prompt_len": len(prompt_text),
                "response_hash": record.response_hash,
                "response_len": len(response_text),
            })
        
        # Auto-flush if buffer is large
        if len(self.records) > 1000:
            self._flush()
    
    def _flush(self):
        """Flush records to disk."""
        with self.lock:
            if not self.records:
                return
            
            # Write to daily JSONL file
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_path = self.analytics_dir / f"requests-{date_str}.jsonl"
            
            with open(log_path, "a") as f:
                for record in self.records:
                    f.write(json.dumps({
                        "ts": record.timestamp,
                        "provider": record.provider,
                        "model": record.model,
                        "complexity": record.complexity,
                        "ctx_tokens": record.context_tokens,
                        "elapsed_ms": record.elapsed_ms,
                        "success": record.success,
                        "error": record.error,
                        "prompt_hash": record.prompt_hash,
                        "prompt_len": record.prompt_length,
                        "response_hash": record.response_hash,
                        "response_len": record.response_length,
                    }) + "\n")
            
            self.records = []
            
            # Flush prompt store periodically
            if len(self.prompt_store) > 5000:
                prompt_path = self.prompt_dir / f"prompts-{date_str}.jsonl"
                with open(prompt_path, "a") as f:
                    for entry in self.prompt_store:
                        f.write(json.dumps(entry) + "\n")
                self.prompt_store = []
    
    def generate_report(self) -> Dict:
        """Generate aggregate usage report (L3 data exhaust)."""
        self._flush()  # flush before reporting
        
        # Collect stats from disk
        total_requests = 0
        provider_counts: Dict[str, int] = {}
        complexity_counts: Dict[str, int] = {}
        total_elapsed = 0.0
        success_count = 0
        
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = self.analytics_dir / f"requests-{date_str}.jsonl"
        
        if log_path.exists():
            for line in open(log_path):
                try:
                    rec = json.loads(line)
                    total_requests += 1
                    provider_counts[rec["provider"]] = provider_counts.get(rec["provider"], 0) + 1
                    complexity_counts[rec["complexity"]] = complexity_counts.get(rec["complexity"], 0) + 1
                    total_elapsed += rec.get("elapsed_ms", 0)
                    if rec.get("success"):
                        success_count += 1
                except json.JSONDecodeError:
                    pass
        
        # Estimate savings vs OpenAI
        # OpenAI ~$2.50/M input, $10/M output. Average request: 20K input, 500 output
        # Cost without proxy: ~$0.055/request
        # With proxy (free): $0.00
        estimated_savings_per_request = 0.055
        
        return {
            "report_date": date_str,
            "total_requests": total_requests,
            "success_rate": success_count / max(total_requests, 1),
            "avg_latency_ms": total_elapsed / max(total_requests, 1),
            "provider_distribution": provider_counts,
            "complexity_distribution": complexity_counts,
            "estimated_savings_vs_openai": round(total_requests * estimated_savings_per_request, 2),
            "most_used_provider": max(provider_counts, key=provider_counts.get) if provider_counts else "none",
            "free_vs_paid_ratio": _compute_free_ratio(provider_counts),
        }
    
    def export_training_dataset(self) -> Path:
        """Export prompt-response pairs for sale (L6 prompt brokerage)."""
        self._flush()
        
        export_path = self.analytics_dir / "exports" / f"dataset-{int(time.time())}.jsonl"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Collect all stored prompts
        all_entries = []
        for f in sorted(self.prompt_dir.glob("prompts-*.jsonl")):
            for line in open(f):
                try:
                    all_entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        
        # Package as training dataset
        with open(export_path, "w") as out:
            for entry in all_entries:
                out.write(json.dumps({
                    "complexity": entry["complexity"],
                    "prompt_hash": entry["prompt_hash"],
                    "prompt_len": entry["prompt_len"],
                    "response_len": entry["response_len"],
                }) + "\n")
        
        return export_path


def _compute_free_ratio(counts: Dict[str, int]) -> float:
    free = sum(v for k, v in counts.items() if "free" in k or k in ("local_qwen", "nvidia_nim"))
    total = sum(counts.values())
    return free / max(total, 1)
