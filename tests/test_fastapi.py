"""FastAPI-сервер: вход, изоляция пользователей, Origin, лимиты; репозиторий и поиск.

Без GigaChat и интернета: APP_OFFLINE=1, эмбеддинги подменены.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from assistant_ai import AssistantAI, SQLiteRepository
from assistant_ai.service import _records

NOW = datetime(2026, 10, 26, 19, 6, tzinfo=ZoneInfo('Europe/Moscow'))
ORIGIN = 'http://localhost:5500'


class FakePlanner:
    def __init__(self, mapping):
        self.mapping, self.catalogs = mapping, []

    def plan(self, text, context, now, catalog):
        self.catalogs.append(catalog)
        return self.mapping.get(text, {'action': 'unknown'})


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ.update(
            APP_OFFLINE='1',
            APP_EMBEDDINGS='off',  # не грузить модель ради API-тестов
            APP_STT_PRELOAD='0',  # и Whisper тоже
            APP_DB_PATH=str(Path(cls.tmp.name) / 'api.sqlite3'),
            APP_CORS_ORIGIN=ORIGIN,
            APP_AUTH_REQUIRED='1',
        )
        from fastapi.testclient import TestClient
        import fastapi_server
        cls.app = fastapi_server.app
        cls.client_cm = TestClient(cls.app)
        cls.client = cls.client_cm.__enter__()  # запускает lifespan

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)
        cls.tmp.cleanup()

    def account(self, name):
        """Отдельный клиент со своими cookie, вошедший как name."""
        from fastapi.testclient import TestClient
        client = TestClient(self.app)
        r = client.post('/api/register', json={'username': name, 'password': 'secret-pass-1'})
        self.assertEqual(r.status_code, 200, r.text)
        return client

    def say(self, client, text, **extra):
        return client.post('/api/assistant', json={'text': text, **extra})

    def test_assistant_requires_login(self):
        r = self.client.post('/api/assistant', json={'text': 'Что у меня сегодня?'})
        self.assertEqual(r.status_code, 401)

    def test_register_login_logout(self):
        client = self.account('anna')
        self.assertEqual(client.get('/api/me').json()['user']['username'], 'anna')
        self.assertEqual(client.post('/api/register', json={'username': 'ANNA', 'password': 'secret-pass-1'}).status_code, 409)
        self.assertEqual(client.post('/api/login', json={'username': 'anna', 'password': 'wrong-pass'}).status_code, 401)
        r = client.post('/api/login', json={'username': 'anna', 'password': 'secret-pass-1'})
        self.assertEqual(r.status_code, 200)
        token = r.json()['token']
        # Bearer работает так же, как cookie
        self.assertEqual(self.client.get('/api/me', headers={'Authorization': 'Bearer ' + token}).status_code, 200)
        client.post('/api/logout')
        self.assertEqual(client.get('/api/me').status_code, 401)

    def test_bad_credentials_rejected(self):
        self.assertEqual(self.client.post('/api/register', json={'username': 'a', 'password': 'secret-pass-1'}).status_code, 400)
        self.assertEqual(self.client.post('/api/register', json={'username': 'bob_ok', 'password': 'short'}).status_code, 400)

    def test_login_throttled(self):
        self.account('victim')
        codes = [self.client.post('/api/login', json={'username': 'victim', 'password': 'nope-nope'}).status_code
                 for _ in range(11)]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(codes[10], 429)

    def test_users_are_isolated(self):
        alice, bob = self.account('alice'), self.account('bob')
        r = self.say(alice, 'Напомни завтра в 10:00 позвонить Ивану')
        self.assertEqual(r.json()['status'], 'success', r.json())
        mine = self.say(alice, 'Что у меня завтра?').json()
        theirs = self.say(bob, 'Что у меня завтра?').json()
        self.assertIn('Ивану', mine['display_text'])
        self.assertNotIn('Ивану', theirs['display_text'])
        # user_id из тела игнорируется при включённом входе
        forged = self.say(bob, 'Что у меня завтра?', user_id='user:1').json()
        self.assertNotIn('Ивану', forged['display_text'])

    def test_items_panel(self):
        self.assertEqual(self.client.get('/api/items').status_code, 401)
        owner, other = self.account('lister'), self.account('stranger')
        self.assertEqual(owner.get('/api/items').json(), {'items': []})
        self.say(owner, 'Напомни послезавтра в 09:00 оплатить интернет')
        self.say(owner, 'Напомни завтра в 10:00 позвонить маме')
        items = owner.get('/api/items').json()['items']
        self.assertEqual([x['title'] for x in items], ['Позвонить маме', 'Оплатить интернет'])
        self.assertEqual(items[0]['type'], 'reminder')
        self.assertNotIn('user_id', items[0])
        self.assertNotIn('deleted', items[0])
        # чужие записи не видны, user_id из адреса игнорируется
        self.assertEqual(other.get('/api/items', params={'user_id': 'user:1'}).json(), {'items': []})
        evil = owner.get('/api/items', headers={'Origin': 'https://evil.example'})
        self.assertEqual(evil.status_code, 403)

    def test_request_id_is_idempotent(self):
        client = self.account('idem')
        first = self.say(client, 'Напомни завтра в 12:00 полить цветы', request_id='r-1').json()
        again = self.say(client, 'Напомни завтра в 12:00 полить цветы', request_id='r-1').json()
        self.assertEqual(first, again)
        listing = self.say(client, 'Что у меня завтра?').json()
        self.assertEqual(listing['display_text'].count('цветы'), 1)

    def test_origin_checks(self):
        client = self.account('orig')
        body = {'text': 'Что у меня сегодня?'}
        evil = client.post('/api/assistant', json=body, headers={'Origin': 'https://evil.example'})
        self.assertEqual(evil.status_code, 403)
        ok = client.post('/api/assistant', json=body, headers={'Origin': ORIGIN})
        self.assertEqual(ok.status_code, 200)
        same_host = client.post('/api/assistant', json=body, headers={'Origin': 'http://testserver'})
        self.assertEqual(same_host.status_code, 200)

    def test_body_limits(self):
        client = self.account('limits')
        r = client.post('/api/assistant', json={'text': 'а' * 70000})
        self.assertEqual(r.status_code, 413)

    def test_transcribe_protected(self):
        r = self.client.post('/api/transcribe', files={'audio': ('a.webm', b'x', 'audio/webm')})
        self.assertEqual(r.status_code, 401)
        client = self.account('voice')
        r = client.post('/api/transcribe', files={'audio': ('a.txt', b'x', 'text/plain')})
        self.assertEqual(r.status_code, 415)
        r = client.post('/api/transcribe', files={'audio': ('a.webm', b'x', 'audio/webm')},
                        headers={'Origin': 'https://evil.example'})
        self.assertEqual(r.status_code, 403)

    def test_web_page_served(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/html', r.headers['content-type'])


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = SQLiteRepository(':memory:')

    def tearDown(self):
        self.repo.close()

    def make(self, title, kind='task', **extra):
        return self.repo.create_item('u', {'type': kind, 'title': title, **extra}, request_id=title)

    def test_update_validation(self):
        item = self.make('отчёт')
        for bad in ({'type': 'party'}, {'status': 'maybe'}, {'title': '   '},
                    {'duration_minutes': 0}, {'duration_minutes': 5000}, {'owner': 'x'}):
            with self.assertRaises(ValueError, msg=bad):
                self.repo.update_item('u', item['id'], bad)
        updated = self.repo.update_item('u', item['id'], {'title': '  ' + 'я' * 300})
        self.assertEqual(len(updated['title']), 250)

    def test_cleanup_drops_old_exchanges_and_sessions(self):
        self.repo.save_exchange('u', 's', 'r', {'status': 'success'})
        self.repo.save_session('u', 's', {'history': []})
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        self.repo.connection.execute('UPDATE exchanges SET updated_at=?', (old,))
        self.repo.connection.execute('UPDATE sessions SET updated_at=?', (old,))
        self.repo.cleanup()
        self.assertIsNone(self.repo.get_exchange('u', 's', 'r'))
        self.assertEqual(self.repo.get_session('u', 's'), {})

    def test_auth_session_expires(self):
        user_id = self.repo.create_user('kate', 'hash')
        self.assertIsNone(self.repo.create_user('KATE', 'hash'))
        self.repo.create_auth_session('t1', user_id, datetime.now(timezone.utc) + timedelta(days=1))
        self.repo.create_auth_session('t2', user_id, datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertEqual(self.repo.get_auth_user('t1')['username'], 'kate')
        self.assertIsNone(self.repo.get_auth_user('t2'))


class FakeEmbedder:
    """Вектор = признаки смысловых групп; отличает 'про что' запись."""
    TOPICS = (('презентац', 'слайд', 'выступлен'), ('договор', 'контракт'), ('мам', 'родител'))

    def __init__(self):
        self.fail, self.name, self.texts = False, 'fake-v1', 0

    def __call__(self, texts):
        if self.fail:
            raise RuntimeError('402')
        self.texts += len(texts)
        return [[1.0 if any(w in t.lower() for w in group) else 0.0 for group in self.TOPICS]
                for t in texts]


class SemanticSearchTests(unittest.TestCase):
    def setUp(self):
        self.embedder = FakeEmbedder()
        self.repo = SQLiteRepository(':memory:', embedder=self.embedder, semantic_threshold=0.75)
        for title in ('Подготовить слайды к выступлению', 'Позвонить маме', 'Купить хлеб'):
            self.repo.create_item('u', {'type': 'task', 'title': title}, request_id=title)

    def tearDown(self):
        self.repo.close()

    def titles(self, query):
        return [x['title'] for x in self.repo.search_items('u', query)]

    def test_finds_by_meaning(self):
        self.assertEqual(self.titles('задача про презентацию'), ['Подготовить слайды к выступлению'])
        self.assertEqual(self.titles('связаться с родителями'), ['Позвонить маме'])
        self.assertEqual(self.titles('погода в Москве'), [])

    def test_lexical_hits_come_first(self):
        self.assertEqual(self.titles('купить хлеб')[0], 'Купить хлеб')

    def test_falls_back_to_lexical_when_api_fails(self):
        self.embedder.fail = True
        self.assertEqual(self.titles('купить хлеб'), ['Купить хлеб'])
        self.assertEqual(self.titles('связаться с родителями'), [])  # только по смыслу

    def test_old_records_get_vectors_on_search(self):
        self.repo.connection.execute('DELETE FROM embeddings')
        self.assertEqual(self.titles('связаться с родителями'), ['Позвонить маме'])

    def test_vector_refreshed_after_rename(self):
        item = self.repo.search_items('u', 'купить хлеб')[0]
        self.repo.update_item('u', item['id'], {'title': 'Подписать контракт'})
        self.assertEqual(self.titles('договор'), ['Подписать контракт'])

    def test_vectors_recomputed_when_model_changes(self):
        self.embedder.texts = 0
        self.titles('связаться с родителями')
        self.assertEqual(self.embedder.texts, 1)  # векторы записей уже есть
        self.embedder.name = 'fake-v2'
        self.assertEqual(self.titles('связаться с родителями'), ['Позвонить маме'])
        self.assertEqual(self.embedder.texts, 1 + 1 + 3)  # запрос + все записи заново

    def test_margin_drops_weaker_matches(self):
        repo = SQLiteRepository(':memory:', embedder=lambda texts: [
            [1.0, 0.0] if 'слайд' in t.lower() else [0.8, 0.6] if 'отчёт' in t.lower() else [0.0, 1.0]
            for t in texts], semantic_threshold=0.5, semantic_margin=0.1)
        for title in ('Подготовить слайды', 'Сдать отчёт'):
            repo.create_item('u', {'type': 'task', 'title': title}, request_id=title)
        self.assertEqual([x['title'] for x in repo.search_items('u', 'про слайды')], ['Подготовить слайды'])
        repo.close()


class EmbedderChoiceTests(unittest.TestCase):
    def tearDown(self):
        for key in ('APP_EMBEDDINGS', 'APP_EMBEDDINGS_MODEL'):
            os.environ.pop(key, None)

    def test_provider_selection(self):
        from assistant_ai.factory import make_embedder
        from assistant_ai.embeddings import GigaChatEmbedder, LocalEmbedder
        os.environ['APP_EMBEDDINGS'] = 'off'
        self.assertIsNone(make_embedder(None))
        os.environ['APP_EMBEDDINGS'] = 'gigachat'
        self.assertIsNone(make_embedder(None))  # офлайн: GigaChat недоступен
        self.assertIsInstance(make_embedder(object()), GigaChatEmbedder)
        os.environ['APP_EMBEDDINGS'] = 'local'
        os.environ['APP_EMBEDDINGS_MODEL'] = 'no-such/model'
        local = make_embedder(None)
        self.assertIsInstance(local, LocalEmbedder)
        os.environ['APP_EMBEDDINGS'] = 'bert'
        with self.assertRaises(RuntimeError):
            make_embedder(None)

    def test_broken_local_model_falls_back_to_words(self):
        from assistant_ai.embeddings import LocalEmbedder
        embedder = LocalEmbedder('no-such/model', background=False)
        repo = SQLiteRepository(':memory:', embedder=embedder)
        repo.create_item('u', {'type': 'task', 'title': 'Купить хлеб'}, request_id='1')
        self.assertEqual([x['title'] for x in repo.search_items('u', 'купить хлеб')], ['Купить хлеб'])
        repo.close()


@unittest.skipUnless(os.getenv('APP_TEST_LOCAL_EMBEDDINGS') == '1',
                     'живая модель: APP_TEST_LOCAL_EMBEDDINGS=1 (скачает ~1 ГБ при первом запуске)')
class LocalModelTests(unittest.TestCase):
    """Сценарии поиска из ТЗ на настоящей локальной модели."""

    @classmethod
    def setUpClass(cls):
        from assistant_ai.embeddings import LocalEmbedder
        cls.repo = SQLiteRepository(':memory:', embedder=LocalEmbedder(background=False))
        for kind, title in (('task', 'Подготовить слайды к выступлению'), ('meeting', 'Встреча с Иваном'),
                            ('task', 'Подписать контракт с поставщиком'), ('reminder', 'Позвонить маме'),
                            ('task', 'Отправить отчет'), ('meeting', 'Стоматолог'), ('task', 'Купить хлеб')):
            cls.repo.create_item('u', {'type': kind, 'title': title}, request_id=title)

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()

    def first(self, query):
        rows = self.repo.search_items('u', query)
        return rows[0]['title'] if rows else None

    def test_tz_queries(self):
        self.assertEqual(self.first('Найди задачу про презентацию'), 'Подготовить слайды к выступлению')
        self.assertEqual(self.first('Я когда-то ставил задачу по договору, найди её'), 'Подписать контракт с поставщиком')
        self.assertEqual(self.first('Когда у меня была встреча с Иваном?'), 'Встреча с Иваном')
        self.assertEqual(self.first('сходить к зубному'), 'Стоматолог')
        self.assertEqual(self.first('связаться с родителями'), 'Позвонить маме')

    def test_no_false_matches(self):
        for query in ('погода в Москве', 'рецепт борща', 'поиграть в футбол'):
            self.assertEqual(self.repo.search_items('u', query), [], query)


class ServiceFixesTests(unittest.TestCase):
    def make(self, plans):
        self.repo = SQLiteRepository(':memory:')
        self.planner = FakePlanner(plans)
        return AssistantAI(self.planner, self.repo, now_func=lambda: NOW)

    def tearDown(self):
        if hasattr(self, 'repo'):
            self.repo.close()

    def day_with_records(self):
        from assistant_ai.rule_planner import RulePlanner
        self.repo = SQLiteRepository(':memory:')
        ai = AssistantAI(RulePlanner(), self.repo, now_func=lambda: NOW)
        for phrase in ('Напомни завтра в 10:00 позвонить Ивану', 'Добавь встречу с Иваном завтра в 12:00',
                       'Напомни завтра отчислить студента в 19:30', 'Поставь задачу купить билеты завтра',
                       'Добавь встречу с командой завтра в 21:00'):
            self.assertEqual(ai.handle_message(phrase)['status'], 'success', phrase)
        return ai

    def test_list_filtered_by_time_of_day(self):
        ai = self.day_with_records()
        cases = {
            'Что у меня завтра после 19:30': ['Отчислить студента', 'Встреча с командой'],
            'Что у меня завтра после 7 вечера': ['Отчислить студента', 'Встреча с командой'],
            'Что у меня завтра с 10 до 12': ['Позвонить Ивану', 'Встреча с Иваном'],
            'Что у меня завтра до 11:00': ['Позвонить Ивану'],
            'Что у меня завтра вечером': ['Отчислить студента', 'Встреча с командой'],
            'Какие планы на завтра утром': ['Позвонить Ивану'],
            'Что у меня завтра после семи вечера': ['Отчислить студента', 'Встреча с командой'],
            'Что у меня завтра с девяти до одиннадцати': ['Позвонить Ивану'],
            'Что у меня завтра после обеда': ['Отчислить студента', 'Встреча с командой'],
            'Что у меня завтра до обеда': ['Позвонить Ивану', 'Встреча с Иваном'],
        }
        for phrase, titles in cases.items():
            result = ai.handle_message(phrase)
            self.assertEqual([x['title'] for x in result['items']], titles, phrase)
        # дата, а не время: «до 15 ноября» не фильтрует часы
        self.assertEqual(len(ai.handle_message('Что у меня завтра до 15 ноября')['items']), 5)

    def test_list_kind_only_when_user_names_it(self):
        # GigaChat отвечает kind=task на «дела»: встречи и напоминания не должны пропасть
        ai = self.day_with_records()
        ai.planner = FakePlanner({
            'Какие дела у меня завтра утром?': {'action': 'list', 'kind': 'task', 'temporal_text': 'завтра утром'},
            'Какие задачи у меня завтра?': {'action': 'list', 'kind': 'task', 'temporal_text': 'завтра'},
        })
        dela = ai.handle_message('Какие дела у меня завтра утром?')
        self.assertEqual([x['title'] for x in dela['items']], ['Позвонить Ивану'])
        tasks = ai.handle_message('Какие задачи у меня завтра?')
        self.assertEqual([x['title'] for x in tasks['items']], ['Купить билеты'])

    def test_list_text_without_repeated_kind(self):
        ai = self.day_with_records()
        result = ai.handle_message('Что у меня завтра?')
        self.assertIn('• 12:00 — Встреча с Иваном\n', result['display_text'] + '\n')
        self.assertIn('Позвонить Ивану · напоминание', result['display_text'])
        speech = result['speech_text']
        self.assertNotIn('(', speech)
        self.assertNotIn('•', speech)
        self.assertIn('Завтра: задача: купить билеты; в 10:00 напоминание: позвонить Ивану; в 12:00 встреча с Иваном', speech)

    def test_title_without_time_words_when_model_omits_title(self):
        # Ответы GigaChat Lite без title: время и «у меня» не попадают в название.
        ai = self.make({
            'Напомни через два часа выпить таблетку':
                {'action': 'create', 'kind': 'reminder', 'temporal_text': 'через два часа'},
            'В четверг в 11 утра у меня встреча с дизайнером':
                {'action': 'create', 'kind': 'meeting', 'temporal_text': 'в четверг в 11 утра'},
        })
        titles = [ai.handle_message(p)['item']['title'] for p in self.planner.mapping]
        self.assertEqual(titles, ['Выпить таблетку', 'Встреча с дизайнером'])

    def test_mark_done_when_model_puts_name_in_title(self):
        ai = self.make({
            'Поставь задачу купить билеты': {'action': 'create', 'kind': 'task', 'title': 'купить билеты'},
            'Отметь задачу купить билеты выполненной':
                {'action': 'update', 'kind': 'task', 'status': 'done', 'title': 'Купить билеты'},
        })
        ai.handle_message('Поставь задачу купить билеты')
        result = ai.handle_message('Отметь задачу купить билеты выполненной')
        self.assertEqual(result['status'], 'success', result['display_text'])
        self.assertEqual(result['item']['status'], 'done')

    def test_undated_tasks_listed_for_today(self):
        ai = self.make({
            'Поставь задачу купить билеты': {'action': 'create', 'kind': 'task', 'title': 'купить билеты'},
            'Что у меня сегодня?': {'action': 'list', 'temporal_text': 'сегодня'},
            'Что у меня на следующей неделе?': {'action': 'list', 'temporal_text': 'на следующей неделе'},
        })
        created = ai.handle_message('Поставь задачу купить билеты')
        self.assertEqual(created['status'], 'success', created)
        today = ai.handle_message('Что у меня сегодня?')
        self.assertIn('Без даты', today['display_text'])
        self.assertIn('купить билеты', today['display_text'].lower())
        later = ai.handle_message('Что у меня на следующей неделе?')
        self.assertNotIn('купить билеты', later['display_text'].lower())

    def test_planner_gets_newest_records(self):
        ai = self.make({})
        for i in range(120):
            self.repo.create_item('demo', {'type': 'task', 'title': f'задача {i}'}, request_id=str(i))
        ai.handle_message('что-то непонятное')
        catalog = self.planner.catalogs[-1]
        self.assertEqual(len(catalog), 100)
        self.assertEqual(catalog[0]['title'], 'задача 119')
        self.assertNotIn('задача 0', [x['title'] for x in catalog])

    # Планы ниже — то, что реально вернул GigaChat: у update нет
    # ни temporal_text, ни reference, а title бывает мусорным.
    RESCHEDULE_PLANS = {
        'Напомни завтра в 9:00 купить молоко':
            {'action': 'create', 'kind': 'reminder', 'title': 'купить молоко', 'temporal_text': 'завтра в 9:00'},
        'Добавь встречу с командой завтра в 15:00':
            {'action': 'create', 'kind': 'meeting', 'title': 'встреча с командой', 'temporal_text': 'завтра в 15:00'},
        'Добавь задачу полить цветы завтра в 8:00':
            {'action': 'create', 'kind': 'task', 'title': 'полить цветы', 'temporal_text': 'завтра в 8:00'},
        'А перенеси её на послезавтра': {'action': 'update', 'title': 'купить молоко'},
        'Перенеси её на послезавтра': {'action': 'update', 'title': 'не указано'},
        'Перенеси встречу с командой на пятницу': {'action': 'update', 'kind': 'meeting', 'title': 'встреча с командой'},
        'Перенеси встречу с командой на 17:00': {'action': 'update', 'kind': 'meeting', 'title': 'встреча с командой'},
        'Перенеси её на послезавтра без времени': {'action': 'update'},
    }

    def reschedule(self, *phrases):
        ai = self.make(self.RESCHEDULE_PLANS)
        results = [ai.handle_message(p) for p in phrases]
        for phrase, result in zip(phrases, results):
            self.assertEqual(result['status'], 'success', (phrase, result['display_text']))
        return results[-1]['item']

    def test_reschedule_by_pronoun_keeps_time(self):
        # NOW = 26.10 19:06 → «завтра» 27.10, «послезавтра» 28.10
        item = self.reschedule('Напомни завтра в 9:00 купить молоко', 'А перенеси её на послезавтра')
        self.assertEqual(item['due_at'], '2026-10-28T09:00:00+03:00')

    def test_reschedule_meeting_to_day_keeps_time(self):
        item = self.reschedule('Добавь встречу с командой завтра в 15:00', 'Перенеси её на послезавтра')
        self.assertEqual(item['start_at'], '2026-10-28T15:00:00+03:00')

    def test_reschedule_by_name_to_weekday(self):
        item = self.reschedule('Добавь встречу с командой завтра в 15:00', 'Перенеси встречу с командой на пятницу')
        self.assertEqual(item['start_at'], '2026-10-30T15:00:00+03:00')

    def test_reschedule_time_only_keeps_day(self):
        item = self.reschedule('Добавь встречу с командой завтра в 15:00', 'Перенеси встречу с командой на 17:00')
        self.assertEqual(item['start_at'], '2026-10-27T17:00:00+03:00')

    def test_reschedule_without_time_on_request(self):
        item = self.reschedule('Добавь задачу полить цветы завтра в 8:00', 'Перенеси её на послезавтра без времени')
        self.assertEqual(item['due_at'], '2026-10-28')

    def test_reference_extraction(self):
        from assistant_ai.service import AssistantAI as A
        cases = {
            'А перенеси её на послезавтра': 'ее',
            'Пожалуйста, перенеси встречу с Анной на 29 октября в 17:00': 'встречу с анной',
            'Перенеси встречу с командой через два дня': 'встречу с командой',
            'Перенеси встречу с Анной на следующую пятницу': 'встречу с анной',
            'Перенеси встречу с Анной во вторник': 'встречу с анной',
            # день недели внутри названия не отрезается
            'Перенеси задачу про среду обитания на завтра': 'задачу про среду обитания',
            'Удали встречу со средой': 'встречу со средой',
        }
        for phrase, expected in cases.items():
            self.assertEqual(A._reference_from_command(phrase), expected, phrase)

    def test_plural(self):
        self.assertEqual([_records(n) for n in (1, 2, 5, 11, 21, 22, 112)],
                         ['1 запись', '2 записи', '5 записей', '11 записей', '21 запись', '22 записи', '112 записей'])


if __name__ == '__main__':
    unittest.main()
