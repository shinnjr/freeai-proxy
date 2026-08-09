"""
Task classifier — determines request complexity for routing decisions.
Uses keyword heuristics + pattern matching (fast, free, no extra API call).
"""

import re
from typing import Dict, List, Tuple


# Complexity scoring patterns
PATTERNS: Dict[str, List[Tuple[str, int]]] = {
    # Trivial: single-step, no reasoning needed
    "trivial": [
        (r"\b(hello|hi|hey|thanks|ok|yes|no|what\'?s?\s+up)\b", 2),
        (r"\b(ping|test|testing)\b", 2),
        (r"^\s*(exit|quit|bye|see ya|goodbye)\s*$", 2),
    ],
    # Simple: straightforward questions, basic tool calls
    "simple": [
        (r"\b(what\s+is|how\s+do\s+I|how\s+to|explain|define)\b", 1),
        (r"\b(list|show|display|get|fetch|read)\b", 1),
        (r"\b(search|find|lookup|check)\b", 1),
        (r"\b(summarize|summarise|tldr|brief)\b", 1),
        (r"\b(translate|convert)\b", 1),
    ],
    # Moderate: multi-step, some planning needed
    "moderate": [
        (r"\b(build|create|write|implement|develop|code|program)\b", 2),
        (r"\b(fix|debug|repair|resolve|troubleshoot)\b", 2),
        (r"\b(analyze|compare|evaluate|review|audit)\b", 2),
        (r"\b(optimize|improve|refactor|enhance)\b", 2),
        (r"\b(design|architect|plan|structure)\b", 2),
        (r"\b(deploy|ship|publish|release)\b", 1),
        (r"\b(test|validate|verify)\b", 1),
        (r"\b(migrate|upgrade|update|patch)\b", 2),
    ],
    # Complex: requires deep reasoning, long planning, multi-file work
    "complex": [
        (r"\b(refactor\s+(entire|whole|full|complete))\b", 3),
        (r"\b(from\s+scratch|greenfield|new\s+project)\b", 3),
        (r"\b(security\s+audit|vulnerability|exploit)\b", 3),
        (r"\b(architecture\s+decision|system\s+design)\b", 3),
        (r"\b(multi[- ]agent|orchestrat|pipeline)\b", 3),
        (r"\b(fine[- ]?tun|train\s+(a|the)\s+model)\b", 3),
        (r"\b(distributed|scal(e|ing|able)|cluster)\b", 3),
        (r"\b(>?\s*500\s*(lines?|LOC|files?))\b", 2),
        (r"\b(comprehensive|exhaustive|deep[-\s]?dive)\b", 2),
    ],
    # Critical: financial, medical, legal, irreversible
    "critical": [
        (r"\b(production|prod\s+deploy|live\s+system)\b", 4),
        (r"\b(database\s+(migration|schema|drop|delete))\b", 4),
        (r"\b(delete|remove|purge|destroy)\s+(all|every|permanent)\b", 4),
        (r"\b(credentials?|password|secret|token|api[-\s]?key)\b", 3),
        (r"\b(financial|payment|billing|transaction|money|crypto)\b", 4),
        (r"\b(legal|compliance|GDPR|HIPAA|regulation)\b", 4),
        (r"\b(medical|health|diagnosis|treatment|patient)\b", 4),
    ],
}

# Tool-call heuristics (presence of tools = higher complexity)
TOOL_SIGNALS = [
    "tool_calls",
    "function_call",
    '"name":',
    "computer_use",
    "browser_use",
    "delegate_task",
    "write_file",
    "patch",
    "terminal",
]


def classify(prompt: str, has_tools: bool = False, context_tokens: int = 0) -> str:
    """
    Classify request complexity.
    
    Returns: 'trivial' | 'simple' | 'moderate' | 'complex' | 'critical'
    """
    prompt_lower = prompt.lower()
    scores: Dict[str, int] = {k: 0 for k in PATTERNS}
    
    for complexity, patterns in PATTERNS.items():
        for pattern, weight in patterns:
            matches = len(re.findall(pattern, prompt_lower, re.IGNORECASE))
            scores[complexity] += matches * weight
    
    # Tool calls raise minimum complexity
    if has_tools:
        for signal in TOOL_SIGNALS:
            if signal in prompt:
                scores["moderate"] += 2
                break
    
    # Context size raises complexity
    if context_tokens > 100000:
        scores["complex"] += 1
    if context_tokens > 200000:
        scores["complex"] += 2
    
    # Prompt length heuristics
    word_count = len(prompt_lower.split())
    if word_count > 500:
        scores["complex"] += 1
    if word_count > 1000:
        scores["complex"] += 2
    
    # Determine winner (highest-scoring complexity that has >0 score)
    scored = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    
    # If literally nothing matches, default to simple
    if scored[0][1] == 0:
        return "simple"
    
    return scored[0][0]


# ── Response for agent-friendly classification ──────────────────

def classify_for_agent(prompt: str, has_tools: bool = False, context_tokens: int = 0) -> Dict:
    """Full classification with routing recommendations."""
    complexity = classify(prompt, has_tools, context_tokens)
    
    from config import CONFIG
    max_tier = CONFIG.complexity_tiers.get(complexity, 3)
    
    return {
        "complexity": complexity,
        "max_provider_tier": max_tier,
        "can_use_free": max_tier <= 3,
        "recommended_provider": "free_tier" if max_tier <= 3 else "cheap_paid",
        "estimated_savings_vs_openai": {
            "trivial": "100% (free)",
            "simple": "100% (free)",
            "moderate": "~95%",
            "complex": "~80%",
            "critical": "~50% (routes to best available)",
        }.get(complexity, "~90%"),
    }
