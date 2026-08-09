"""CLI entry point for FreeAI Proxy."""
import sys, os, subprocess

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "start":
        install_dir = os.path.expanduser("~/.freeai")
        if not os.path.exists(install_dir):
            print("Installing FreeAI Proxy...")
            subprocess.run(
                "curl -fsSL https://freeai-proxy.pages.dev/install.sh | bash",
                shell=True, check=True
            )
        else:
            subprocess.run([sys.executable, os.path.join(install_dir, "proxy-server")])
    elif cmd == "stop":
        subprocess.run(["killall", "python3"], check=False)
    elif cmd == "status":
        import httpx
        try:
            r = httpx.get("http://127.0.0.1:11434/health", timeout=5)
            print(r.json())
        except:
            print("Proxy not running. Start with: freeai-proxy start")

if __name__ == "__main__":
    main()
