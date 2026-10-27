"""LOCAL development REST bridge for STT/frontend colleagues.

Not a public production server. It binds 127.0.0.1 by default and trusts
client-supplied user_id ONLY for local demo. Production MUST use Django auth.
"""
import os
import json
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import RLock
from assistant_ai.factory import make_application

MAX_BODY=8192


def build_handler(app,allowed_origin='',token=''):
    lock=RLock()
    class Handler(BaseHTTPRequestHandler):
        def _headers(self,status=200):
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            if allowed_origin and self.headers.get('Origin') == allowed_origin:
                self.send_header('Access-Control-Allow-Origin',allowed_origin)
                self.send_header('Vary','Origin')
                self.send_header('Access-Control-Allow-Headers','Content-Type, Authorization')
                self.send_header('Access-Control-Allow-Methods','POST, OPTIONS, GET')
            self.end_headers()
        def _send(self,status,payload):
            self._headers(status)
            self.wfile.write(json.dumps(payload,ensure_ascii=False,default=str).encode('utf-8'))
        def do_OPTIONS(self):
            origin=self.headers.get('Origin')
            if origin and origin!=allowed_origin:return self._send(403,{'error':'origin_forbidden'})
            self._headers(204)
        def do_GET(self):
            if self.path=='/health':return self._send(200,{'ok':True,'component':'assistant_ai'})
            self._send(404,{'error':'not_found'})
        def do_POST(self):
            if self.path!='/api/assistant':return self._send(404,{'error':'not_found'})
            origin=self.headers.get('Origin')
            if origin and (not allowed_origin or origin!=allowed_origin):
                return self._send(403,{'error':'origin_forbidden'})
            if token and self.headers.get('Authorization')!='Bearer '+token:
                return self._send(401,{'error':'unauthorized'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=MAX_BODY:return self._send(413,{'error':'invalid_size'})
                incoming=json.loads(self.rfile.read(length))
                if not isinstance(incoming,dict) or not isinstance(incoming.get('text'),str):
                    return self._send(400,{'error':'text_required'})
                text=incoming['text'].strip()
                user=str(incoming.get('user_id') or 'demo')[:100]
                session=str(incoming.get('session_id') or 'browser')[:100]
                request_id=incoming.get('request_id')
                if request_id is not None and (not isinstance(request_id,str) or not 1<=len(request_id)<=120):
                    return self._send(400,{'error':'invalid_request_id'})
                if not text:return self._send(400,{'error':'empty_text'})
                with lock:
                    repo=app.crud
                    previous=(repo.get_exchange(user,session,request_id) if request_id and hasattr(repo,'get_exchange') else None)
                    result=previous if previous is not None else app.handle_message(text,user_id=user,session_id=session)
                    if previous is None and request_id and result.get('status') != 'error' and hasattr(repo,'save_exchange'):
                        repo.save_exchange(user,session,request_id,result)
                self._send(200,result)
            except (UnicodeDecodeError,ValueError,TypeError,json.JSONDecodeError):
                self._send(400,{'error':'invalid_json'})
            except Exception:
                self._send(500,{'error':'internal_error'})
        def log_message(self,fmt,*args):
            # Avoid printing utterances, account data or credentials in logs.
            pass
    return Handler


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--offline',action='store_true',help='diagnostic basic parser (no GigaChat)')
    p.add_argument('--port',type=int,default=8765)
    args=p.parse_args()
    if not 1<=args.port<=65535:raise SystemExit('Неверный порт')
    app,client=make_application(offline=args.offline)
    from dotenv import load_dotenv
    load_dotenv()
    allowed_origin=os.getenv('APP_CORS_ORIGIN','')
    token=os.getenv('APP_API_TOKEN','')
    srv=ThreadingHTTPServer(('127.0.0.1',args.port),build_handler(app,allowed_origin,token))
    print(f'AssistantAI REST: http://127.0.0.1:{args.port}/api/assistant')
    print('Только localhost. Для Django используйте авторизацию сервера, а не client user_id.')
    try:srv.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        srv.server_close()
        if client is not None:client.close()
        if hasattr(app.crud,'close'):app.crud.close()

if __name__=='__main__':main()
