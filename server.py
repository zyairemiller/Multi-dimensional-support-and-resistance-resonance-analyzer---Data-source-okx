"""
Trading Signal Live Server - Lightweight HTTP Server

Provides HTML static file serving + OKX API proxy to resolve local file:// protocol CORS issues.
API proxy uses requests + SOCKS5 proxy scheme (consistent with okx_data.py),
replacing the original urllib.request direct connection to ensure proxy traversal.

Usage:
  python server.py                  # Default port 8080
  python server.py --port 9090      # Specify port
  python server.py --no-open        # Do not auto-open browser
"""

import http.server
import json
import requests
import os
import webbrowser
import argparse
from urllib.parse import urlparse, parse_qs

# SOCKS5 proxy initialization (shared proxy config with okx_data.py)
try:
    from okx_data import _make_request, _USE_PROXY, _SOCKS5_HOST, _SOCKS5_PORT
    _HAS_OKX_PROXY = True
except ImportError:
    _HAS_OKX_PROXY = False
    _USE_PROXY = False


class TradingHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP request handler providing static file serving and API proxy"""

    def do_GET(self):
        parsed = urlparse(self.path)

        # API proxy: /api/tickers?instType=SWAP or /api/tickers?instType=SPOT
        if parsed.path == '/api/tickers':
            params = parse_qs(parsed.query)
            inst_type = params.get('instType', ['SWAP'])[0]
            okx_url = f'https://www.okx.com/api/v5/market/tickers?instType={inst_type}'

            try:
                # Use requests + SOCKS5 proxy (consistent with okx_data.py scheme)
                proxies = None
                if _USE_PROXY:
                    proxies = {
                        "http": f"socks5://{_SOCKS5_HOST}:{_SOCKS5_PORT}",
                        "https": f"socks5://{_SOCKS5_HOST}:{_SOCKS5_PORT}",
                    }
                resp = requests.get(
                    okx_url,
                    headers={'User-Agent': 'Mozilla/5.0'},
                    timeout=10,
                    proxies=proxies
                )
                data = resp.content
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'code': '-1', 'msg': str(e)}).encode())
            return

        # Static file serving: if accessing /, auto-find the latest HTML file
        if parsed.path == '/' or parsed.path == '':
            html_files = [
                f for f in os.listdir('.')
                if f.startswith('trading_signal_') and f.endswith('.html')
            ]
            if html_files:
                html_files.sort(reverse=True)
                self.path = '/' + html_files[0]
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write('No trading signal HTML found'.encode())
                return

        # Other files handled normally (static file serving by SimpleHTTPRequestHandler)
        return super().do_GET()

    def log_message(self, format, *args):
        """Custom log format"""
        print(f"  [{self.log_date_time_string()}] {format % args}")


def main():
    parser = argparse.ArgumentParser(description='Trading Signal Live Server')
    parser.add_argument(
        '--port',
        type=int,
        default=8080,
        help='Port to serve on (default: 8080)'
    )
    parser.add_argument(
        '--no-open',
        action='store_true',
        help='Do not auto-open browser'
    )
    args = parser.parse_args()

    # Switch to script directory to ensure HTML files can be found
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    server = http.server.HTTPServer(('127.0.0.1', args.port), TradingHandler)
    url = f'http://127.0.0.1:{args.port}'
    print(f'Trading Signal Server running at {url}')
    print(f'   Press Ctrl+C to stop')

    if not args.no_open:
        # Delay opening browser after startup to ensure server is ready
        import threading
        def open_browser():
            import time
            time.sleep(1.5)  # Wait for server to be ready
            opened = False
            try:
                import webbrowser
                webbrowser.open(url)
                opened = True
                print(f'   Browser opened: {url}')
            except Exception as e:
                print(f'   webbrowser open failed: {e}')
            if not opened and os.name == 'nt':
                try:
                    os.startfile(url)
                    print(f'   Browser opened (os.startfile): {url}')
                except Exception as e2:
                    print(f'   Auto-open browser failed: {e2}')
                    print(f'   Please visit manually: {url}')

        t = threading.Thread(target=open_browser, daemon=True)
        t.start()

    print(f'\n   If browser did not open automatically, please visit: {url}')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nServer stopped')
        server.server_close()


if __name__ == '__main__':
    main()
