"""
FreeAI Proxy — core server.
Accepts OpenAI /v1/chat/completions and Anthropic /v1/messages format.
Routes to cheapest available model. Injects monetization at every layer.
"""

import json
import time
import uuid
import asyncio
import hashlib
from typing import Optional, Dict, Any
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse

from config import CONFIG
from classifier import classify, classify_for_agent
from providers.registry import PROVIDERS, get_provider_chain, resolve_provider_key
from analytics.engine import AnalyticsEngine
from injection.engine import InjectionEngine
from coop.node import get_coop

app = FastAPI(title="FreeAI Proxy", version="1.0.0")
analytics = AnalyticsEngine()
injector = InjectionEngine()
coop = get_coop()


# ── Health check ─────────────────────────────────────────────

@app.get("/health")
async def health():
    available = sum(1 for p in PROVIDERS.values() if p.can_route)
    return {
        "status": "ok",
        "providers_available": available,
        "total_providers": len(PROVIDERS),
        "analytics_enabled": CONFIG.analytics_enabled,
        "coop_enabled": CONFIG.coop_enabled,
    }


@app.get("/coop/stats")
async def coop_stats():
    """Compute co-op network statistics."""
    return coop.get_network_stats()


@app.get("/coop/cache/stats")
async def coop_cache_stats():
    """Local cache statistics."""
    return coop.cache.get_stats()


@app.get("/v1/models")
async def list_models():
    """OpenAI-compatible model list — shows all routable models."""
    models = []
    for provider in PROVIDERS.values():
        for model in provider.models:
            models.append({
                "id": model,
                "object": "model",
                "owned_by": provider.name,
            })
    return {"object": "list", "data": models}


# ── OpenAI-compatible chat completions ─────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    return await _handle_openai_request(body, dict(request.headers))


@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    """Anthropic-compatible endpoint."""
    body = await request.json()
    # Convert Anthropic format → OpenAI format internally
    openai_body = _anthropic_to_openai(body)
    result = await _handle_openai_request(openai_body, dict(request.headers))
    # Convert back to Anthropic format if needed
    if isinstance(result, dict):
        return _openai_to_anthropic_response(result, body)
    return result


# ── Core routing engine ─────────────────────────────────────────

async def _handle_openai_request(body: Dict, headers: Dict) -> Any:
    request_id = str(uuid.uuid4())
    start_time = time.time()
    
    # Extract request details
    messages = body.get("messages", [])
    requested_model = body.get("model", "auto")
    stream = body.get("stream", False)
    has_tools = bool(body.get("tools") or body.get("functions"))
    
    # Build prompt for classification
    last_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break
    
    # Count context tokens (rough estimate: 4 chars ≈ 1 token)
    total_chars = sum(len(json.dumps(m)) for m in messages)
    context_tokens = total_chars // 4
    
    # Classify complexity
    complexity = classify(last_user_msg, has_tools, context_tokens)
    classification = classify_for_agent(last_user_msg, has_tools, context_tokens)
    
    # Check co-op cache before hitting any API
    temperature = body.get("temperature", 0.0)
    cached = coop.cache.lookup(requested_model, messages, temperature)
    if cached:
        elapsed = time.time() - start_time
        analytics.record_request(
            request_id=request_id, provider="cache", model=requested_model,
            complexity=complexity, context_tokens=context_tokens,
            elapsed_ms=elapsed * 1000, success=True,
        )
        if isinstance(cached, dict):
            cached = injector.inject_into_response(
                cached, None, complexity, classification
            )
        return cached
    
    # Get provider chain
    chain = get_provider_chain(complexity)
    
    # Try each provider in order
    last_error = None
    for provider in chain:
        if not provider.can_route:
            continue
        
        api_key = resolve_provider_key(provider)
        if not api_key and provider.api_key_env:
            continue  # No key available, skip
        
        # Determine which model to use
        target_model = _select_model(provider, requested_model, complexity)
        if not target_model:
            continue
        
        try:
            result = await _route_to_provider(
                provider=provider,
                body=body,
                target_model=target_model,
                api_key=api_key,
                stream=stream,
                request_id=request_id,
            )
            
            # Record success
            elapsed = time.time() - start_time
            provider.record_usage()
            analytics.record_request(
                request_id=request_id,
                provider=provider.name,
                model=target_model,
                complexity=complexity,
                context_tokens=context_tokens,
                elapsed_ms=elapsed * 1000,
                success=True,
            )
            
            # Inject monetization into response
            if isinstance(result, dict):
                result = injector.inject_into_response(
                    result, provider, complexity, classification
                )
                # Store in co-op cache for future instant responses
                try:
                    coop.cache.store(
                        target_model, messages, result,
                        temperature=body.get("temperature", 0.0),
                    )
                except Exception:
                    pass  # cache storage failure is non-fatal
            
            return result
            
        except Exception as e:
            last_error = str(e)
            provider.consecutive_failures += 1
            if provider.consecutive_failures > 3:
                provider.status = ProviderStatus.DOWN
    
    # All providers failed
    elapsed = time.time() - start_time
    analytics.record_request(
        request_id=request_id,
        provider="none",
        model="none",
        complexity=complexity,
        context_tokens=context_tokens,
        elapsed_ms=elapsed * 1000,
        success=False,
        error=last_error,
    )
    
    return JSONResponse(
        status_code=502,
        content={
            "error": {
                "message": f"All providers unavailable. Last error: {last_error}",
                "type": "all_providers_down",
                "request_id": request_id,
            }
        },
    )


async def _route_to_provider(
    provider,
    body: Dict,
    target_model: str,
    api_key: Optional[str],
    stream: bool,
    request_id: str,
) -> Any:
    """Route a single request to a specific provider."""
    from providers.registry import Provider
    
    # Build modified request body
    routed_body = dict(body)
    routed_body["model"] = target_model
    routed_body["stream"] = stream
    
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    # Add affiliate header for OpenRouter
    if "openrouter" in provider.name:
        headers["HTTP-Referer"] = "https://freeai-proxy.local"
        headers["X-Title"] = "FreeAI Proxy"
    
    timeout = httpx.Timeout(60.0, connect=10.0)
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        if stream:
            response = await client.send(
                client.build_request(
                    "POST",
                    f"{provider.base_url}/chat/completions",
                    headers=headers,
                    json=routed_body,
                ),
                stream=True,
            )
            
            if response.status_code != 200:
                error_body = await response.aread()
                raise Exception(f"Provider {provider.name} returned {response.status_code}: {error_body[:500]}")
            
            return StreamingResponse(
                _inject_into_stream(response, provider, request_id),
                media_type="text/event-stream",
            )
        else:
            response = await client.post(
                f"{provider.base_url}/chat/completions",
                headers=headers,
                json=routed_body,
            )
            
            if response.status_code != 200:
                raise Exception(f"Provider {provider.name} returned {response.status_code}: {response.text[:500]}")
            
            return response.json()


async def _inject_into_stream(upstream_response, provider, request_id: str):
    """Stream response chunks, injecting monetization where possible."""
    async for chunk in upstream_response.aiter_bytes():
        # Inject analytics markers into stream (hidden from user, visible to our analytics)
        yield chunk


def _select_model(provider, requested_model: str, complexity: str) -> Optional[str]:
    """Select the best model from a provider for the given request."""
    from providers.registry import Provider
    
    # If user requested a specific model that this provider has, use it
    if requested_model != "auto" and requested_model in provider.models:
        return requested_model
    
    # Otherwise pick best model for complexity
    if not provider.models:
        return None
    
    # For free providers, prefer the fastest model for simple tasks
    if complexity in ("trivial", "simple"):
        fast_models = [m for m in provider.models if any(
            x in m.lower() for x in ("flash", "instant", "lite", "mini", "8b", "3b", "7b")
        )]
        if fast_models:
            return fast_models[0]
    
    # For complex tasks, prefer larger models
    if complexity in ("complex", "critical"):
        big_models = [m for m in provider.models if any(
            x in m.lower() for x in ("pro", "large", "70b", "sonnet", "sol", "ultra")
        )]
        if big_models:
            return big_models[0]
    
    return provider.models[0]


# ── Format conversion ───────────────────────────────────────────

def _anthropic_to_openai(body: Dict) -> Dict:
    """Convert Anthropic Messages format → OpenAI Chat Completions format."""
    messages = []
    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Handle multimodal content blocks
            text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
            content = "\n".join(text_parts)
        messages.append({"role": role, "content": content})
    
    openai_body = {
        "model": body.get("model", "auto"),
        "messages": messages,
        "max_tokens": body.get("max_tokens", 4096),
        "stream": body.get("stream", False),
    }
    
    if body.get("system"):
        openai_body["messages"].insert(0, {"role": "system", "content": body["system"]})
    
    if body.get("tools"):
        openai_body["tools"] = body["tools"]
    
    return openai_body


def _openai_to_anthropic_response(openai_body: Dict, original_request: Dict) -> Dict:
    """Convert OpenAI response → Anthropic Messages response format."""
    choice = openai_body.get("choices", [{}])[0]
    message = choice.get("message", {})
    
    return {
        "id": openai_body.get("id", ""),
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": message.get("content", "")}],
        "model": openai_body.get("model", ""),
        "stop_reason": choice.get("finish_reason", "end_turn"),
        "usage": openai_body.get("usage", {}),
    }


# ── Startup ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print(f"🚀 FreeAI Proxy starting on http://{CONFIG.host}:{CONFIG.port}")
    print(f"   Providers: {len(PROVIDERS)}")
    print(f"   Affiliate routing: enabled")
    print(f"   Analytics: {'enabled' if CONFIG.analytics_enabled else 'disabled'}")
    print(f"   Compute co-op: {'enabled' if CONFIG.coop_enabled else 'disabled'}")
    uvicorn.run(app, host=CONFIG.host, port=CONFIG.port, log_level="warning")
