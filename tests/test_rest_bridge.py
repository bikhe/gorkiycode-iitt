"""Real local HTTP integration tests, no external internet or API credentials."""
import unittest
import json
import tempfile
import urllib.request
import urllib.error
import threading
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer
from assistant_ai import AssistantAI,SQLiteRepository
from assistant_ai.rule_planner import RulePlanner
from server import build_handler

NOW=datetime(2026,10,26,14,0,tzinfo=ZoneInfo('Europe/Moscow'))

class RestBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.repository=SQLiteRepository(str(Path(cls.temp.name)/'test.db'))
        cls.app=AssistantAI(RulePlanner(),cls.repository,now_func=lambda:NOW)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),build_handler(cls.app,'http://localhost:5500','secret-local-token'))
        cls.port=cls.server.server_port
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join(3);cls.server.server_close();cls.repository.close();cls.temp.cleanup()
    def request(self,data=None,path='/api/assistant',auth=True,origin=None,method=None):
        payload=json.dumps(data,ensure_ascii=False).encode() if data is not None else None
        headers={'Content-Type':'application/json'}
        if auth:headers['Authorization']='Bearer secret-local-token'
        if origin:headers['Origin']=origin
        req=urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,data=payload,headers=headers,method=method or ('POST' if payload is not None else 'GET'))
        try:
            with urllib.request.urlopen(req,timeout=3) as resp:
                return resp.status,json.loads(resp.read() or '{}')
        except urllib.error.HTTPError as error:
            return error.code,json.loads(error.read() or '{}')
    def test_health(self):
        code,data=self.request(path='/health');self.assertEqual(code,200);self.assertTrue(data['ok'])
    def test_api_shape(self):
        code,data=self.request({'text':'Напомни завтра купить чай','user_id':'restA','session_id':'s1','request_id':'t1'})
        self.assertEqual(code,200)
        self.assertEqual(data['status'],'success')
        self.assertIn('display_text',data);self.assertIn('speech_text',data)
    def test_idempotent_request(self):
        item={'text':'Напомни завтра купить чай','user_id':'restB','session_id':'s1','request_id':'t2'}
        first=self.request(item)[1];second=self.request(item)[1]
        self.assertEqual(first,second)
        self.assertEqual(len(self.repository.list_items('restB','2026-10-27','2026-10-27')),1)
    def test_invalid_text(self):
        code,_=self.request({'wrong':'hello'});self.assertEqual(code,400)
    def test_missing_token(self):
        code,_=self.request({'text':'Привет'},auth=False);self.assertEqual(code,401)
    def test_blocked_origin(self):
        code,_=self.request({'text':'Привет'},origin='https://evil.example');self.assertEqual(code,403)
    def test_good_origin(self):
        code,_=self.request({'text':'Что у меня завтра?'},origin='http://localhost:5500');self.assertEqual(code,200)
    def test_unknown_path(self):
        code,_=self.request(path='/not-here');self.assertEqual(code,404)
    def test_other_user_scope(self):
        self.request({'text':'Напомни завтра купить чай','user_id':'onlyA','session_id':'one','request_id':'t3'})
        _,data=self.request({'text':'Что у меня завтра?','user_id':'onlyB','session_id':'one','request_id':'t4'})
        self.assertEqual(data['items'],[])

if __name__=='__main__':unittest.main()
