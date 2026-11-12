"""TLS-only Cloudflare boundary for the isolated production-image rehearsal."""
import json
import ssl
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

secret = Path('/fixture/secret').read_text()
used_tokens = set()


class Siteverify(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        token = payload.get('response', '')
        accepted = (self.path == '/turnstile/v0/siteverify' and payload.get('secret') == secret
                    and token.startswith('memory-check-') and token not in used_tokens)
        if accepted:
            used_tokens.add(token)
        result = {'success': True, 'hostname': 'heyhanzi.com', 'action': 'login',
                  'challenge_ts': datetime.now(timezone.utc).isoformat()} if accepted else {
                      'success': False, 'error-codes': ['invalid-input-response']}
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('/fixture/cert.pem', '/fixture/key.pem')
server = HTTPServer(('0.0.0.0', 443), Siteverify)
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
