"""Single-process engine + local dashboard. Defaults to live engine observation."""
from __future__ import annotations
import argparse
import asyncio
import json
import signal
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .telemetry import OfficeTelemetry

STATIC = Path(__file__).resolve().parent / 'static'

def make_handler(telemetry, demo=False):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(STATIC), **kwargs)

        def do_GET(self):
            port = self.server.server_address[1]
            hosts = {f'localhost:{port}', f'127.0.0.1:{port}'}
            if self.headers.get('Host') not in hosts:
                self.send_error(403)
                return
            path = self.path.split('?', 1)[0]
            if path == '/api/state':
                origin = self.headers.get('Origin')
                if ((origin and origin not in {f'http://{h}' for h in hosts}) or
                        self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                    self.send_error(403)
                    return
                snapshot = telemetry.snapshot()
                snapshot['mode'] = 'demo' if demo else 'live'
                data = json.dumps(snapshot, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(data)
            elif path in {'/', '/index.html', '/app.js', '/style.css'}:
                super().do_GET()
            else:
                self.send_error(404)

        def do_HEAD(self):
            self.send_error(405)

        def log_message(self, *_args):
            pass
    return Handler

async def boot(telemetry, seconds):
    from sme.app import SmartMoneyEngine
    from sme.config import get_settings
    from .runtime import run_observed
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    timer = loop.call_later(seconds, stop.set) if seconds else None
    try:
        await run_observed(SmartMoneyEngine(get_settings()), telemetry, stop)
    finally:
        if timer:
            timer.cancel()

def main(argv=None):
    parser = argparse.ArgumentParser(description='PULSE engine + R4ISE Office; Pulse is CEO')
    parser.add_argument('--demo', action='store_true', help='Run simulated office only; do not start engine')
    parser.add_argument('--check', action='store_true', help='Print redacted configuration, without network requests')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--seconds', type=float, default=0, help='Stop after N seconds; 0 means until Ctrl+C')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or args.seconds < 0:
        parser.error('port must be 1..65535; seconds must be nonnegative')
    if args.check and args.demo:
        parser.error('Use either --check or --demo')
    telemetry = OfficeTelemetry()
    if args.check:
        from sme.app import SmartMoneyEngine
        from sme.config import get_settings
        from .runtime import sync_engine
        settings = get_settings()
        settings.validate_security()
        sync_engine(telemetry, SmartMoneyEngine(settings))
        print(json.dumps({'chains': telemetry.snapshot()['chains'],
                          'fomo_enabled': settings.fomo_enabled,
                          'fomo_key_configured': bool((settings.fomo_api_key or '').strip()),
                          'telegram_enabled': settings.telegram_enabled,
                          'discord_enabled': settings.discord_enabled}, indent=2))
        return
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(telemetry, args.demo))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f'R4ISE Office: http://127.0.0.1:{args.port}', flush=True)
    print('DEMO: engine is not started.' if args.demo else 'LIVE: starting PULSE with the existing .env.', flush=True)
    try:
        if args.demo:
            thread.join(timeout=args.seconds or None)
        else:
            asyncio.run(boot(telemetry, args.seconds))
    except KeyboardInterrupt:
        pass
    finally:
        telemetry.set_running(False)
        server.shutdown()
        server.server_close()

if __name__ == '__main__':
    main()
