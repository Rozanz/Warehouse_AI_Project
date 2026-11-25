from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json, os
from pathlib import Path
import engine as e
class Handler(BaseHTTPRequestHandler):
    def send(self,data,kind='application/json',status=200):
        self.send_response(status);self.send_header('Content-Type',kind);self.end_headers();self.wfile.write(data if isinstance(data,bytes) else json.dumps(data).encode())
    def do_GET(self):
        if self.path=='/':return self.send(Path(__file__).with_name('index.html').read_bytes(),'text/html; charset=utf-8')
        if self.path=='/api/mode':return self.send({'ai_enabled':os.getenv('WAREHOUSE_AI','0')=='1','model':os.getenv('OLLAMA_MODEL','qwen2.5:7b')})
        if self.path=='/api/state':return self.send({k:e.rows('SELECT * FROM '+k+' ORDER BY rowid DESC') for k in ('orders','tickets','logs','metrics','inventory')})
        self.send({},status=404)
    def do_POST(self):
        try:
            d=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path=='/api/request':r=e.request(d.get('owner'),d.get('order'),d.get('message'),d.get('timeout',False))
            elif self.path=='/api/decision':r={'result':e.decide(d['id'],d['approve'])}
            elif self.path=='/api/followup':r={'result':e.close_followup(d['id'])}
            elif self.path=='/api/ship':r={'allowed':e.ship(d['id'])}
            elif self.path=='/api/scale':r=e.scale(d['n'])
            elif self.path=='/api/reset':e.reset_demo();r={'reset':True}
            else:return self.send({},status=404)
            self.send(r,status=400 if r.get('outcome')=='INVALID' else 200)
        except (ValueError,KeyError,TypeError):self.send({'error':'Invalid request'},status=400)
if __name__=='__main__':
    e.init();print('Open http://127.0.0.1:8000',flush=True);ThreadingHTTPServer(('127.0.0.1',8000),Handler).serve_forever()
