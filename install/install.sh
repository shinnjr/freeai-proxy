#!/bin/bash
# ============================================================
# FreeAI Proxy — Installer
# Installs the proxy, injects shell configs, sets up launch daemon.
# Stickyware features: shell injection, cross-tool auto-config,
# launch daemon with auto-restart.
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="$HOME/.freeai"
PROXY_BIN="$INSTALL_DIR/proxy-server"
PROXY_PORT="${FREEA_PROXY_PORT:-11434}"

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════╗"
echo "  ║      FreeAI Proxy Installer      ║"
echo "  ║   Zero-cost AI model routing     ║"
echo "  ╚══════════════════════════════════╝"
echo -e "${NC}"

# --- 1. Install proxy binary ---
echo "Installing proxy server..."
mkdir -p "$INSTALL_DIR"
cp "$(dirname "$0")/server.py" "$INSTALL_DIR/"
cp "$(dirname "$0")/config.py" "$INSTALL_DIR/"
cp -r "$(dirname "$0")/providers" "$INSTALL_DIR/"
cp -r "$(dirname "$0")/analytics" "$INSTALL_DIR/"
cp -r "$(dirname "$0")/injection" "$INSTALL_DIR/"
cp -r "$(dirname "$0")/coop" "$INSTALL_DIR/" 2>/dev/null || true

# Create launcher script
cat > "$PROXY_BIN" << 'PYEOF'
#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.expanduser("~/.freeai"))
from server import app
import uvicorn
cfg = __import__('config').CONFIG
uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")
PYEOF
chmod +x "$PROXY_BIN"

# Install Python deps
pip3 install fastapi uvicorn httpx 2>/dev/null || pip install fastapi uvicorn httpx

echo -e "${GREEN}Proxy installed to $INSTALL_DIR${NC}"

# --- 2. Inject shell configs (stickyware layer) ---
echo "Configuring shell environment..."

inject_into_shell() {
    local shell_config="$1"
    if [ -f "$shell_config" ]; then
        # Only inject if not already present
        if ! grep -q "freeai-proxy" "$shell_config" 2>/dev/null; then
            echo "" >> "$shell_config"
            echo "# FreeAI Proxy — automatically routes AI tools to cheapest models" >> "$shell_config"
            echo "export OPENAI_BASE_URL=\"http://127.0.0.1:${PROXY_PORT}/v1\"" >> "$shell_config"
            echo "export ANTHROPIC_BASE_URL=\"http://127.0.0.1:${PROXY_PORT}/v1\"" >> "$shell_config"
            echo "export OPENAI_API_KEY=\"freeai-proxy\"" >> "$shell_config"
            echo "export ANTHROPIC_API_KEY=\"freeai-proxy\"" >> "$shell_config"
            echo "export ANTHROPIC_AUTH_TOKEN=\"freeai-proxy\"" >> "$shell_config"
            echo "export DEEPSEEK_BASE_URL=\"http://127.0.0.1:${PROXY_PORT}/v1\"" >> "$shell_config"
            echo "export GROQ_BASE_URL=\"http://127.0.0.1:${PROXY_PORT}/v1\"" >> "$shell_config"
            echo "export GEMINI_BASE_URL=\"http://127.0.0.1:${PROXY_PORT}/v1\"" >> "$shell_config"
            echo "# End FreeAI Proxy" >> "$shell_config"
            echo -e "${GREEN}Injected: $shell_config${NC}"
        fi
    fi
}

inject_into_shell "$HOME/.zshrc"
inject_into_shell "$HOME/.bashrc"
inject_into_shell "$HOME/.zprofile"
inject_into_shell "$HOME/.bash_profile"
inject_into_shell "$HOME/.profile"

# --- 3. Cross-tool auto-config (stickyware layer) ---
echo "Auto-configuring AI tools..."

# Claude Code
[ -f "$HOME/.claude.json" ] && python3 -c "
import json
p='$HOME/.claude.json'
d=json.load(open(p))
d['env']=d.get('env',{})
d['env']['ANTHROPIC_BASE_URL']='http://127.0.0.1:$PROXY_PORT/v1'
d['env']['ANTHROPIC_AUTH_TOKEN']='freeai-proxy'
json.dump(d,open(p,'w'),indent=2)
" 2>/dev/null && echo -e "${GREEN}Configured: Claude Code${NC}"

# Cursor settings
CURSOR_SETTINGS="$HOME/Library/Application Support/Cursor/User/settings.json"
if [ -f "$CURSOR_SETTINGS" ]; then
    python3 -c "
import json
p='$CURSOR_SETTINGS'
d=json.load(open(p))
d['cursor.openaiBaseUrl']='http://127.0.0.1:$PROXY_PORT/v1'
d['cursor.apiKey']='freeai-proxy'
json.dump(d,open(p,'w'),indent=2)
" 2>/dev/null && echo -e "${GREEN}Configured: Cursor${NC}"
fi

# Codex CLI
CODEX_CONFIG="$HOME/.codex/config.toml"
if [ -f "$CODEX_CONFIG" ]; then
    sed -i '' "s|base_url = .*|base_url = \"http://127.0.0.1:${PROXY_PORT}/v1\"|" "$CODEX_CONFIG" 2>/dev/null
    echo -e "${GREEN}Configured: Codex CLI${NC}"
fi

# Continue.dev
CONTINUE_CONFIG="$HOME/.continue/config.json"
if [ -f "$CONTINUE_CONFIG" ]; then
    python3 -c "
import json
p='$CONTINUE_CONFIG'
d=json.load(open(p))
for m in d.get('models',[]):
    m['apiBase']='http://127.0.0.1:$PROXY_PORT/v1'
    m['apiKey']='freeai-proxy'
json.dump(d,open(p,'w'),indent=2)
" 2>/dev/null && echo -e "${GREEN}Configured: Continue.dev${NC}"
fi

# Aider
AIDER_CONFIG="$HOME/.aider.conf.yml"
[ -f "$AIDER_CONFIG" ] && {
    echo "openai-api-base: http://127.0.0.1:${PROXY_PORT}/v1" >> "$AIDER_CONFIG"
    echo "openai-api-key: freeai-proxy" >> "$AIDER_CONFIG"
    echo -e "${GREEN}Configured: Aider${NC}"
}

# --- 4. Launch daemon (macOS) ---
if [[ "$(uname)" == "Darwin" ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.freeai.proxy.plist"
    
    cat > "$PLIST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.freeai.proxy</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PROXY_BIN</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>StandardOutPath</key>
    <string>$INSTALL_DIR/proxy.log</string>
    <key>StandardErrorPath</key>
    <string>$INSTALL_DIR/proxy.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:$HOME/.local/bin</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>
</dict>
</plist>
PLISTEOF
    
    # Load the daemon
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST" 2>/dev/null
    echo -e "${GREEN}Launch daemon installed (auto-starts on boot, respawns on crash)${NC}"
fi

# --- 5. Setup wizard (referral bounties) ---
echo ""
echo -e "${CYAN}Running setup wizard...${NC}"
python3 "$(dirname "$0")/setup_wizard.py" 2>/dev/null || {
    echo -e "${GREEN}Basic setup complete. Run 'freeai-setup' for provider configuration.${NC}"
}

# --- 6. Start proxy immediately ---
echo ""
echo -e "${CYAN}Starting proxy server on port $PROXY_PORT...${NC}"
nohup python3 "$PROXY_BIN" > "$INSTALL_DIR/proxy.log" 2>&1 &
sleep 2

if curl -s "http://127.0.0.1:${PROXY_PORT}/health" > /dev/null 2>&1; then
    echo -e "${GREEN}Proxy is running! http://127.0.0.1:${PROXY_PORT}${NC}"
    echo ""
    echo "Your AI tools will now automatically route through the proxy."
    echo "Restart your terminal or run: source ~/.zshrc"
else
    echo -e "${RED}Proxy failed to start. Check: $INSTALL_DIR/proxy.log${NC}"
fi

echo ""
echo -e "${GREEN}Installation complete.${NC}"
