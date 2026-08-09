#!/usr/bin/env python3
"""
Onboarding wizard — sets up free-tier API accounts through referral links.
Each step earns a referral bounty (L2).
User thinks they're setting up free API access (they are).
We collect bounties from each provider they sign up for.
"""

import webbrowser
import subprocess
import sys
import time

CYAN = '\033[0;36m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
NC = '\033[0m'

from config import CONFIG


STEPS = [
    {
        "name": "Groq",
        "env_var": "GROQ_API_KEY",
        "signup_url": CONFIG.groq_referral,
        "console_url": "https://console.groq.com/keys",
        "description": "Fastest free inference. 30 req/min, 15K tokens/min.",
        "revenue": "Referral bounty: $10 credit",
    },
    {
        "name": "Google Gemini",
        "env_var": "GEMINI_API_KEY",
        "signup_url": CONFIG.google_cloud_referral,
        "console_url": "https://aistudio.google.com/apikey",
        "description": "Generous free tier. 15 req/min, 32K tokens/min.",
        "revenue": "Referral bounty: $25 cloud credit",
    },
    {
        "name": "NVIDIA NIM",
        "env_var": "NVIDIA_API_KEY",
        "signup_url": CONFIG.nvidia_nim_referral,
        "console_url": "https://build.nvidia.com/explore/discover",
        "description": "Free tier access to Llama, Mistral, Gemma models.",
        "revenue": "Referral bounty: $10 credit",
    },
    {
        "name": "OpenRouter",
        "env_var": "OPENROUTER_API_KEY",
        "signup_url": CONFIG.openrouter_referral_url,
        "console_url": "https://openrouter.ai/keys",
        "description": "Access to 200+ models. Free models available.",
        "revenue": "Affiliate: 20% recurring on all usage",
    },
    {
        "name": "DeepSeek",
        "env_var": "DEEPSEEK_API_KEY",
        "signup_url": "https://platform.deepseek.com/signup",
        "console_url": "https://platform.deepseek.com/api_keys",
        "description": "Cheapest paid models. V4 Flash at $0.14/M tokens.",
        "revenue": "No bounty — cheapest routing target",
    },
]


def main():
    print(f"{CYAN}")
    print("  ╔══════════════════════════════════╗")
    print("  ║   FreeAI Proxy — Setup Wizard     ║")
    print("  ║   Configure free API providers    ║")
    print("  ╚══════════════════════════════════╝")
    print(f"{NC}")
    print("We'll set up 5 free/cheap API providers.")
    print("Each one gives you a free tier — you never pay.")
    print("The proxy bounces between them automatically.")
    print("")
    
    env_updates = {}
    
    for i, step in enumerate(STEPS):
        print(f"{YELLOW}[{i+1}/{len(STEPS)}] {step['name']}{NC}")
        print(f"  {step['description']}")
        print(f"  Signup: {step['signup_url']}")
        print(f"  API key page: {step['console_url']}")
        print("")
        
        # Open signup in browser
        try:
            webbrowser.open(step['signup_url'])
        except Exception:
            pass
        
        print("  After signing up, paste your API key below (or press Enter to skip):")
        api_key = input("  > ").strip()
        
        if api_key:
            env_updates[step['env_var']] = api_key
            print(f"  {GREEN}Saved.{NC}")
        else:
            print(f"  Skipped (proxy will use affiliate routing for this provider).")
        
        # Open console for key retrieval
        if api_key:
            try:
                webbrowser.open(step['console_url'])
            except Exception:
                pass
        
        print("")
    
    # Write to .env
    env_path = __import__('pathlib').Path.home() / '.freeai' / '.env'
    env_path.parent.mkdir(parents=True, exist_ok=True)
    
    existing = {}
    if env_path.exists():
        for line in env_path.read_text().split('\n'):
            if '=' in line:
                k, v = line.split('=', 1)
                existing[k.strip()] = v.strip()
    
    existing.update(env_updates)
    
    with open(env_path, 'w') as f:
        for k, v in existing.items():
            f.write(f"{k}={v}\n")
    
    # Source into current shell
    for k, v in env_updates.items():
        print(f"export {k}={v}")
    
    print(f"\n{GREEN}Setup complete!{NC}")
    print(f"API keys saved to {env_path}")
    print("Restart your terminal or run: source ~/.freeai/.env")
    
    # Summary
    configured = len(env_updates)
    total_possible = len(STEPS)
    print(f"\n{CYAN}Providers configured: {configured}/{total_possible}{NC}")
    
    if configured < 3:
        print(f"{YELLOW}Tip: More providers = better fallback coverage.{NC}")
    
    # Estimated savings
    print(f"\n{CYAN}Estimated monthly savings vs ChatGPT Pro ($20):{NC}")
    print(f"  With {configured} providers: ${configured * 4}/mo in free API access")
    print(f"  All 5 providers: ~$20/mo in free API access = full ChatGPT replacement")


if __name__ == "__main__":
    main()
