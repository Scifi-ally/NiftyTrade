"""Main entrypoint launching the NiftyTrades server and algorithmic trading system."""
import argparse
import sys
import uvicorn
from app.config import settings
from app.core.logging import logger

def main():
    parser = argparse.ArgumentParser(description="NiftyTrades Automated Trading System")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0 for LAN/WAN access)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn hot-reload")
    args = parser.parse_args()

    import socket
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    logger.info("=" * 70)
    logger.info("  Starting NiftyTrades: Automated NIFTY Options Trading System")
    logger.info("  Strategy: Clean Signals v4 (Exact Pine Script Port)")
    logger.info(f"  Mode: {settings.EXECUTION_MODE} (Always initialized to PAPER on startup)")
    logger.info(f"  Local Dashboard:    http://localhost:{args.port}")
    logger.info(f"  LAN (Same Wi-Fi):   http://{local_ip}:{args.port}")
    logger.info("=" * 70)

    uvicorn.run(
        "app.web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=settings.LOG_LEVEL.lower()
    )

if __name__ == "__main__":
    main()
