"""Local or trusted-team demo server; hosted mode requires shared credentials and HTTPS proxy."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json, os, base64, hmac, threading
from pathlib import Path
from urllib.parse import urlsplit
import engine as e
HOST=os.getenv('WAREHOUSE_HOST','127.0.0.1')
PORT=int(os.getenv('WAREHOUSE_PORT','8000'))
PASSWORD=os.getenv('WAREHOUSE_DEMO_PASSWORD','')
USER=os.getenv('WAREHOUSE_DEMO_USER','team')
ORIGIN=os.getenv('WAREHOUSE_PUBLIC_ORIGIN','').rstrip('/')
LIMIT=threading.BoundedSemaphore(4)
class Server(ThreadingHTTPServer):
    daemon_threads=True
    def process_request(self,request,address):
        if not LIMIT.acquire(blocking=False):
            try:request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n')
            finally:self.shutdown_request(request)
            return
        try:super().process_request(request,address)
        except Exception:LIMIT.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:LIMIT.release()
class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(30)
    def send(self,data,kind='application/json',status=200):
        body=data if isinstance(data,bytes) else json.dumps(data).encode()
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(body)
    def authorized(self):
        if not PASSWORD:return True
        expected='Basic '+base64.b64encode((USER+':'+PASSWORD).encode()).decode()
        if hmac.compare_digest(self.headers.get('Authorization','').encode(),expected.encode()):return True
        self.send_response(401);self.send_header('WWW-Authenticate','Basic realm="Warehouse team demo"');self.send_header('Content-Length','0');self.end_headers();return False
    def do_GET(self):
        if self.path=='/health':return self.send({'status':'ok'})
        if not self.authorized():return
        if self.path=='/':return self.send(Path(__file__).with_name('index.html').read_bytes(),'text/html; charset=utf-8')
        if self.path=='/api/mode':return self.send({'ai_enabled':os.getenv('WAREHOUSE_AI','0')=='1','model':os.getenv('OLLAMA_MODEL','qwen2.5:7b'),'protected':bool(PASSWORD)})
        if self.path=='/api/state':return self.send({k:e.rows('SELECT * FROM '+k+' ORDER BY rowid DESC') for k in ('orders','tickets','logs','metrics','inventory')})
        self.send({},status=404)
    def do_POST(self):
        if not self.authorized():return
        origin=self.headers.get('Origin')
        allowed=ORIGIN or f'http://127.0.0.1:{PORT}'
        if origin and origin not in (allowed, f'http://localhost:{PORT}' if not PASSWORD else allowed):return self.send({'error':'Origin denied'},status=403)
        if self.headers.get_content_type()!='application/json':return self.send({'error':'JSON required'},status=415)
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=4096:return self.send({'error':'Invalid body length'},status=400)
            d=json.loads(self.rfile.read(n))
            if not isinstance(d,dict):raise ValueError('object required')
            if self.path=='/api/request':
                timeout=d.get('timeout',False)
                if type(timeout) is not bool:raise ValueError('Invalid timeout')
                r=e.request(d.get('owner'),d.get('order'),d.get('message'),timeout)
            elif self.path=='/api/decision':r={'result':e.decide(d['id'],d['approve'])}
            elif self.path=='/api/followup':r={'result':e.close_followup(d['id'])}
            elif self.path=='/api/ship':r={'allowed':e.ship(d['id'])}
            elif self.path=='/api/scale':r=e.scale(d['n'])
            elif self.path=='/api/reset':e.reset_demo();r={'reset':True}
            else:return self.send({},status=404)
            self.send(r,status=400 if r.get('outcome')=='INVALID' else 200)
        except (ValueError,KeyError,TypeError):self.send({'error':'Invalid request'},status=400)
        except Exception:self.send({'error':'Internal error; inspect server logs'},status=500)
if __name__=='__main__':
    if HOST not in ('127.0.0.1','localhost','::1') and (len(PASSWORD)<12 or not ORIGIN.startswith('https://')):raise SystemExit('Hosted mode requires a strong WAREHOUSE_DEMO_PASSWORD (12+ characters) and HTTPS WAREHOUSE_PUBLIC_ORIGIN.')
    e.init();print(f'Listening on {HOST}:{PORT}',flush=True);Server((HOST,PORT),Handler).serve_forever()
