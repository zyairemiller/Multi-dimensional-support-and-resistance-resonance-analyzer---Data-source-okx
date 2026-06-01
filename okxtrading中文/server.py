"""
Trading Signal Live Server - 轻量HTTP服务器

提供HTML页面静态服务 + OKX API代理，解决本地file://协议的CORS问题。
API 代理通过 requests + SOCKS5 代理方案（与 okx_data.py 一致），
替代原 urllib.request 直连，确保代理穿透。

用法:
  python server.py                  # 默认8080端口
  python server.py --port 9090      # 指定端口
  python server.py --no-open        # 不自动打开浏览器
"""

import http.server
import json
import requests
import os
import webbrowser
import argparse
from urllib.parse import urlparse, parse_qs

# SOCKS5 代理初始化（与 okx_data.py 共享同一套代理配置）
try:
    from okx_data import _make_request, _USE_PROXY, _SOCKS5_HOST, _SOCKS5_PORT
    _HAS_OKX_PROXY = True
except ImportError:
    _HAS_OKX_PROXY = False
    _USE_PROXY = False


class TradingHandler(http.server.SimpleHTTPRequestHandler):
    """自定义HTTP请求处理器，提供静态文件服务和API代理"""

    def do_GET(self):
        parsed = urlparse(self.path)

        # API代理：/api/tickers?instType=SWAP 或 /api/tickers?instType=SPOT
        if parsed.path == '/api/tickers':
            params = parse_qs(parsed.query)
            inst_type = params.get('instType', ['SWAP'])[0]
            okx_url = f'https://www.okx.com/api/v5/market/tickers?instType={inst_type}'

            try:
                # 使用 requests + SOCKS5 代理（与 okx_data.py 一致的方案）
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

        # 静态文件服务：如果访问 /，自动找到最新的HTML文件
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

        # 其他文件正常处理（由SimpleHTTPRequestHandler提供静态文件服务）
        return super().do_GET()

    def log_message(self, format, *args):
        """自定义日志格式"""
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

    # 切换到脚本所在目录，确保能找到HTML文件
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    server = http.server.HTTPServer(('127.0.0.1', args.port), TradingHandler)
    url = f'http://127.0.0.1:{args.port}'
    print(f'🚀 Trading Signal Server running at {url}')
    print(f'   Press Ctrl+C to stop')

    if not args.no_open:
        # 启动后延迟打开浏览器，确保服务器就绪
        import threading
        def open_browser():
            import time
            time.sleep(1.5)  # 等待服务器就绪
            opened = False
            try:
                import webbrowser
                webbrowser.open(url)
                opened = True
                print(f'   🌐 浏览器已打开: {url}')
            except Exception as e:
                print(f'   ⚠️ webbrowser打开失败: {e}')
            if not opened and os.name == 'nt':
                try:
                    os.startfile(url)
                    print(f'   🌐 浏览器已打开(os.startfile): {url}')
                except Exception as e2:
                    print(f'   ⚠️ 自动打开浏览器失败: {e2}')
                    print(f'   请手动访问: {url}')

        t = threading.Thread(target=open_browser, daemon=True)
        t.start()

    print(f'\n   💡 如果浏览器未自动打开，请手动访问: {url}')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n👋 Server stopped')
        server.server_close()


if __name__ == '__main__':
    main()
