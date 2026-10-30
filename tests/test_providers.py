"""LLM- и TTS-провайдеры: формат запросов и разбор ответов (HTTP подменён)."""
import base64
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from assistant_ai.openai_planner import OpenAICompatiblePlanner
from voice_tts import GoogleTTS, TTSError, make_tts

NOW = datetime(2026, 10, 26, 19, 6, tzinfo=ZoneInfo('Europe/Moscow'))
PLAN = {'action': 'create', 'kind': 'reminder', 'title': 'позвонить Ивану', 'temporal_text': 'завтра в 10 утра'}


def transport(handler, seen):
    def wrapped(request):
        seen.append(request)
        return handler(request)
    return httpx.MockTransport(wrapped)


class OpenAIPlannerTests(unittest.TestCase):
    def planner(self, handler):
        self.seen = []
        return OpenAICompatiblePlanner('https://llm.example/v1', 'test-model', api_key='sk-test',
                                       transport=transport(handler, self.seen))

    def test_request_and_tool_call(self):
        planner = self.planner(lambda r: httpx.Response(200, json={'choices': [{'message': {
            'tool_calls': [{'type': 'function', 'function': {'name': 'plan_action', 'arguments': json.dumps(PLAN)}}]}}]}))
        self.assertEqual(planner.plan('Напомни завтра в 10 утра позвонить Ивану', {}, NOW, []), PLAN)
        request = self.seen[0]
        self.assertEqual(str(request.url), 'https://llm.example/v1/chat/completions')
        self.assertEqual(request.headers['authorization'], 'Bearer sk-test')
        body = json.loads(request.content)
        self.assertEqual(body['model'], 'test-model')
        self.assertEqual(body['tools'][0]['function']['name'], 'plan_action')
        self.assertEqual(body['messages'][0]['role'], 'system')
        self.assertIn('plan_action', body['messages'][0]['content'])

    def test_legacy_function_call_format(self):
        planner = self.planner(lambda r: httpx.Response(200, json={'choices': [{'message': {
            'function_call': {'name': 'plan_action', 'arguments': json.dumps(PLAN)}}}]}))
        self.assertEqual(planner.plan('x', {}, NOW, []), PLAN)

    def test_plain_text_answer_is_unknown(self):
        planner = self.planner(lambda r: httpx.Response(200, json={'choices': [{'message': {'content': 'Привет!'}}]}))
        self.assertEqual(planner.plan('x', {}, NOW, []), {'action': 'unknown'})

    def test_http_error_raises(self):
        # AssistantAI превращает исключение в status=error, а не в ложное «готово»
        planner = self.planner(lambda r: httpx.Response(401, json={'error': 'bad key'}))
        with self.assertRaises(httpx.HTTPStatusError):
            planner.plan('x', {}, NOW, [])

    def test_requires_url_and_model(self):
        with self.assertRaises(RuntimeError):
            OpenAICompatiblePlanner('', 'model')
        with self.assertRaises(RuntimeError):
            OpenAICompatiblePlanner('https://x/v1', '')


class PlannerChoiceTests(unittest.TestCase):
    KEYS = ('APP_LLM_PROVIDER', 'LLM_BASE_URL', 'LLM_MODEL', 'LLM_API_KEY')

    def tearDown(self):
        for key in self.KEYS:
            os.environ.pop(key, None)

    def test_provider_selection(self):
        from assistant_ai.factory import make_planner
        from assistant_ai.rule_planner import RulePlanner
        self.assertIsInstance(make_planner(offline=True)[0], RulePlanner)
        os.environ.update(APP_LLM_PROVIDER='deepseek', LLM_MODEL='deepseek-chat')
        planner, client = make_planner()
        self.assertIsInstance(planner, OpenAICompatiblePlanner)
        self.assertEqual(str(planner.http.base_url), 'https://api.deepseek.com/v1/')
        client.close()
        os.environ.update(APP_LLM_PROVIDER='openai_compatible', LLM_BASE_URL='http://my-llm:8000/v1')
        planner, client = make_planner()
        self.assertEqual(str(planner.http.base_url), 'http://my-llm:8000/v1/')
        client.close()
        os.environ['APP_LLM_PROVIDER'] = 'skynet'
        with self.assertRaises(RuntimeError):
            make_planner()


class GoogleTTSTests(unittest.TestCase):
    def engine(self, handler, **options):
        self.seen = []
        return GoogleTTS('g-key', transport=transport(handler, self.seen), **options)

    def test_synthesize(self):
        audio = b'ID3-fake-mp3'
        engine = self.engine(lambda r: httpx.Response(200, json={'audioContent': base64.b64encode(audio).decode()}),
                             voice='ru-RU-Wavenet-A', speaking_rate=1.2)
        self.assertEqual(engine.synthesize('Привет'), (audio, 'audio/mpeg'))
        request = self.seen[0]
        self.assertEqual(request.headers['x-goog-api-key'], 'g-key')
        body = json.loads(request.content)
        self.assertEqual(body['input'], {'text': 'Привет'})
        self.assertEqual(body['voice'], {'languageCode': 'ru-RU', 'name': 'ru-RU-Wavenet-A'})
        self.assertEqual(body['audioConfig'], {'audioEncoding': 'MP3', 'speakingRate': 1.2})

    def test_error_hides_details(self):
        engine = self.engine(lambda r: httpx.Response(403, json={'error': {'message': 'API key g-key invalid'}}))
        with self.assertRaises(TTSError) as caught:
            engine.synthesize('Привет')
        self.assertEqual(str(caught.exception), 'google: HTTP 403')

    def test_config_checks(self):
        with self.assertRaises(RuntimeError):
            GoogleTTS('')
        with self.assertRaises(RuntimeError):
            GoogleTTS('k', encoding='FLAC')
        os.environ['APP_TTS_PROVIDER'] = 'browser'
        try:
            self.assertIsNone(make_tts())
            os.environ['APP_TTS_PROVIDER'] = 'robot'
            with self.assertRaises(RuntimeError):
                make_tts()
        finally:
            os.environ.pop('APP_TTS_PROVIDER')


class FakeTTS:
    name = 'fake'

    def __init__(self, fail=False):
        self.fail = fail

    def synthesize(self, text):
        if self.fail:
            raise TTSError('fake: HTTP 500')
        return text.encode(), 'audio/mpeg'

    def close(self):
        pass


class TTSEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ.update(APP_OFFLINE='1', APP_STT_PRELOAD='0', APP_EMBEDDINGS='off', APP_AUTH_REQUIRED='1',
                          APP_DB_PATH=str(Path(cls.tmp.name) / 'tts.sqlite3'))
        from fastapi.testclient import TestClient
        import fastapi_server
        cls.cm = TestClient(fastapi_server.app)
        cls.client = cls.cm.__enter__()
        cls.client.post('/api/register', json={'username': 'speaker', 'password': 'secret-pass-1'})

    @classmethod
    def tearDownClass(cls):
        cls.cm.__exit__(None, None, None)
        cls.tmp.cleanup()

    def setUp(self):
        self.app = self.client.app
        self.saved = self.app.state.tts

    def tearDown(self):
        self.app.state.tts = self.saved

    def test_disabled_by_default(self):
        self.app.state.tts = None
        self.assertEqual(self.client.post('/api/tts', json={'text': 'Привет'}).status_code, 404)
        config = self.client.get('/api/config').json()
        self.assertEqual(config['tts'], 'browser')
        self.assertEqual(config['stt'], 'local')  # по умолчанию распознаёт сервер, без интернета

    def test_returns_audio(self):
        self.app.state.tts = FakeTTS()
        r = self.client.post('/api/tts', json={'text': 'Привет'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers['content-type'], 'audio/mpeg')
        self.assertEqual(r.content, 'Привет'.encode())

    def test_limits_and_errors(self):
        self.app.state.tts = FakeTTS()
        self.assertEqual(self.client.post('/api/tts', json={'text': '  '}).status_code, 400)
        self.assertEqual(self.client.post('/api/tts', json={'text': 'а' * 1001}).status_code, 413)
        self.app.state.tts = FakeTTS(fail=True)
        self.assertEqual(self.client.post('/api/tts', json={'text': 'Привет'}).status_code, 502)

    def test_requires_login(self):
        from fastapi.testclient import TestClient
        self.app.state.tts = FakeTTS()
        anonymous = TestClient(self.app)
        self.assertEqual(anonymous.post('/api/tts', json={'text': 'Привет'}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
