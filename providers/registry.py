"""
Provider registry — every free and cheap AI API, with rate-limit tracking.
"""

import time
import os
import asyncio
import httpx
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum


class ProviderStatus(Enum):
    AVAILABLE = "available"
    RATE_LIMITED = "rate_limited"
    DOWN = "down"
    UNAUTHORIZED = "unauthorized"


@dataclass
class Provider:
    name: str
    base_url: str
    api_key_env: str  # env var for user's key, empty if provider-managed
    models: List[str] = field(default_factory=list)
    is_free: bool = True
    free_rpm_limit: int = 30  # requests per minute
    free_tpm_limit: int = 15000  # tokens per minute
    
    # Runtime state
    status: ProviderStatus = ProviderStatus.AVAILABLE
    rpm_window: List[float] = field(default_factory=list)
    tpm_window: int = 0
    window_reset: float = field(default_factory=time.time)
    consecutive_failures: int = 0
    
    @property
    def can_route(self) -> bool:
        """Check if provider is available and not rate-limited."""
        now = time.time()
        # Reset windows if minute passed
        if now - self.window_reset > 60:
            self.rpm_window = []
            self.tpm_window = 0
            self.window_reset = now
            if self.status == ProviderStatus.RATE_LIMITED:
                self.status = ProviderStatus.AVAILABLE
        
        if self.status != ProviderStatus.AVAILABLE:
            return False
        
        if len(self.rpm_window) >= self.free_rpm_limit:
            self.status = ProviderStatus.RATE_LIMITED
            return False
        
        return True
    
    def record_usage(self, tokens: int = 0):
        """Record a request for rate-limit tracking."""
        self.rpm_window.append(time.time())
        self.tpm_window += tokens


# ── Provider definitions ────────────────────────────────────────

PROVIDERS: Dict[str, Provider] = {
    "groq_free": Provider(
        name="groq_free",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        models=[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ],
        is_free=True,
        free_rpm_limit=30,
        free_tpm_limit=15000,
    ),
    "gemini_free": Provider(
        name="gemini_free",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key_env="GEMINI_API_KEY",
        models=[
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-flash",
        ],
        is_free=True,
        free_rpm_limit=15,
        free_tpm_limit=32000,
    ),
    "nvidia_nim": Provider(
        name="nvidia_nim",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key_env="NVIDIA_API_KEY",
        models=[
            "meta/llama-3.1-70b-instruct",
            "meta/llama-3.1-8b-instruct",
            "mistralai/mistral-large",
            "google/gemma-2-27b-it",
        ],
        is_free=True,
        free_rpm_limit=30,
        free_tpm_limit=30000,
    ),
    "openrouter_free": Provider(
        name="openrouter_free",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",  # will be auto-set to affiliate key
        models=[
            "google/gemini-2.0-flash-001",
            "meta-llama/llama-3.2-3b-instruct",
            "mistralai/mistral-7b-instruct",
        ],
        is_free=True,
        free_rpm_limit=20,
        free_tpm_limit=20000,
    ),
    "deepseek": Provider(
        name="deepseek",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        models=[
            "deepseek-chat",
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        ],
        is_free=False,
        free_rpm_limit=60,
        free_tpm_limit=100000,
    ),
    "openrouter_paid": Provider(
        name="openrouter_paid",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        models=[
            "deepseek/deepseek-v4-flash",
            "deepseek/deepseek-v4-pro",
            "anthropic/claude-sonnet-4",
            "openai/gpt-5.6-sol",
        ],
        is_free=False,
        free_rpm_limit=60,
        free_tpm_limit=200000,
    ),
    "local_qwen": Provider(
        name="local_qwen",
        base_url="http://127.0.0.1:8080/v1",
        api_key_env="",
        models=["qwen3.6-27b"],
        is_free=True,
        free_rpm_limit=999,
        free_tpm_limit=999999,
    ),
}


def get_provider_chain(task_complexity: str = "simple") -> List[Provider]:
    """Return providers in priority order, filtered by complexity tier."""
    from config import CONFIG
    max_tier = CONFIG.complexity_tiers.get(task_complexity, 3)
    
    chain = []
    for name, provider in PROVIDERS.items():
        tier = CONFIG.provider_priority.get(name, 99)
        if tier <= max_tier:
            chain.append((tier, provider))
    
    chain.sort(key=lambda x: x[0])
    return [p for _, p in chain]


def resolve_provider_key(provider: Provider) -> Optional[str]:
    """Get API key for provider. Falls back to affiliate key for OpenRouter."""
    from config import CONFIG
    
    if not provider.api_key_env:
        return None
    
    # Check user env first
    user_key = os.environ.get(provider.api_key_env)
    if user_key:
        return user_key
    
    # OpenRouter gets affiliate key as fallback
    if "openrouter" in provider.name:
        return CONFIG.openrouter_affiliate_key
    
    return None
