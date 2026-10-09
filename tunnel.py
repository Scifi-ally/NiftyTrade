"""Zero-config public internet access launcher using Cloudflare Quick Tunnels.

Provides instant HTTPS access to your NiftyTrades dashboard from anywhere in the world
without needing router port forwarding, static IP, or firewall changes.
"""
import os
import re
import sys
import time
import shutil
import urllib.request
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def get_cloudflared_path() -> str:
    """Locate or automatically download cloudflared standalone binary."""
    # 1. Check PATH
    which_path = shutil.which("cloudflared")
    if which_path:
        return which_path

    # 2. Check local folder
    is_windows = sys.platform.startswith("win")
    bin_name = "cloudflared.exe" if is_windows else "cloudflared"
    local_bin = BASE_DIR / bin_name
    if local_bin.exists():
        return str(local_bin)

    # 3. Download standalone binary if not present
    print("[TUNNEL] cloudflared binary not found. Downloading standalone binary from Cloudflare...")
    if is_windows:
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    elif sys.platform.startswith("linux"):
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    elif sys.platform.startswith("darwin"):
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64"
    else:
        raise RuntimeError(f"Unsupported OS: {sys.platform}")

    try:
        urllib.request.urlretrieve(url, str(local_bin))
        if not is_windows:
            os.chmod(str(local_bin), 0o755)
        print(f"[TUNNEL] Successfully downloaded cloudflared to {local_bin}")
        return str(local_bin)
    except Exception as e:
        print(f"[TUNNEL] Could not auto-download cloudflared: {e}")
        print("Please install cloudflared manually from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        sys.exit(1)

def run_tunnel(port: int = 8000):
    bin_path = get_cloudflared_path()
    cmd = [bin_path, "tunnel", "--url", f"http://127.0.0.1:{port}"]

    print("=" * 70)
    print("  Starting Cloudflare Secure Tunnel for NiftyTrades...")
    print(f"  Target: http://127.0.0.1:{port}")
    print("=" * 70)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    url_found = False
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    # Cloudflare outputs tunnel logs to stderr
    for line in proc.stderr:
        line_clean = line.strip()
        match = url_pattern.search(line_clean)
        if match and not url_found:
            url_found = True
            public_url = match.group(0)
            print("\n" + "=" * 70)
            print("  >>> LIVE INTERNET URL (ACCESSIBLE FROM ANYWHERE ON ANY DEVICE) <<<")
            print(f"  URL: {public_url}")
            print("=" * 70 + "\n")
        elif "ERR" in line_clean:
            print(f"[TUNNEL LOG] {line_clean}")

    proc.wait()

if __name__ == "__main__":
    port_to_forward = 8000
    if len(sys.argv) > 1:
        try:
            port_to_forward = int(sys.argv[1])
        except ValueError:
            pass
    run_tunnel(port_to_forward)
