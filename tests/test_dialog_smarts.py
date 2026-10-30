"""Провалы из живых диалогов 30.10.2026: каждый тест — реальная фраза пользователя."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from assistant_ai import AssistantAI, DemoRepository
from assistant_ai.dates import DateResolver

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
TZ = '+03:00'


class FakePlanner:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def plan(self, text, context, now, catalog):
        self.calls.append(text)
        return self.mapping.get(text) or {'action': 'unknown'}


def make(plans=None):
    repo = DemoRepository()
    planner = FakePlanner(plans or {})
    ai = AssistantAI(planner, repo, now_func=lambda: NOW)
    return ai, repo, planner


def seed(repo):
    """Записи как в базе пользователя: две встречи с Иваном и звонок на завтра."""
    rows = [
        ('meeting', 'Встреча с Иваном', 'start_at', f'2026-10-31T10:00:00{TZ}'),
        ('reminder', 'Позвонить Ивану', 'due_at', f'2026-10-31T10:00:00{TZ}'),
        ('meeting', 'Встреча с Иваном', 'start_at', f'2026-10-31T12:00:00{TZ}'),
        ('reminder', 'Отчислить студента', 'due_at', f'2026-10-31T19:30:00{TZ}'),
    ]
    for i, (kind, title, field, at) in enumerate(rows):
        data = {'type': kind, 'title': title, 'status': 'pending', 'recurrence': None,
                'duration_minutes': 60, 'start_at': None, 'due_at': None}
        data[field] = at
        repo.create_item('demo', data, f'seed-{i}')


class KindClarificationTests(unittest.TestCase):
    def test_answer_to_kind_question_keeps_the_request(self):
        phrase = 'угнать автовышку СУЭС послезавтра в 1:26'
        ai, repo, planner = make({phrase: {
            'action': 'create', 'title': 'угнать автовышку СУЭС',
            'temporal_text': 'послезавтра в 1:26'}})
        first = ai.handle_message(phrase)
        self.assertEqual(first['status'], 'clarification')
        self.assertIn('Задача', first.get('suggestions', []))
        second = ai.handle_message('задача')
        self.assertEqual(second['status'], 'success', second['display_text'])
        self.assertEqual(second['item']['type'], 'task')
        self.assertEqual(second['item']['title'], 'Угнать автовышку СУЭС')
        self.assertEqual(second['item']['due_at'], f'2026-11-01T01:26:00{TZ}')
        self.assertEqual(len(planner.calls), 1, 'ответ на уточнение не должен идти в LLM заново')

    def test_new_command_drops_kind_question(self):
        phrase = 'угнать автовышку послезавтра в 1:26'
        ai, repo, planner = make({phrase: {'action': 'create', 'title': 'угнать автовышку',
                                           'temporal_text': 'послезавтра в 1:26'}})
        ai.handle_message(phrase)
        ai.handle_message('что у меня завтра')
        self.assertEqual(repo.items, [])


class DaytimeClockTests(unittest.TestCase):
    def test_ten_oclock_of_the_day_is_morning(self):
        parser = DateResolver()
        self.assertEqual(parser.clock('встреча завтра в 10 часов дня'), (10, 0))
        self.assertEqual(parser.clock('в 11 дня'), (11, 0))
        self.assertEqual(parser.clock('в 3 часа дня'), (15, 0))
        self.assertEqual(parser.clock('в 12 дня'), (12, 0))


class SearchWithTimeTests(unittest.TestCase):
    def test_search_by_title_and_time(self):
        phrase = 'встреча с иваном которая должна быть завтра в 10 часов дня'
        ai, repo, _ = make({phrase: {'action': 'search', 'query': phrase}})
        seed(repo)
        result = ai.handle_message(phrase)
        self.assertEqual(result['status'], 'success', result['display_text'])
        self.assertEqual([x['title'] for x in result['items']], ['Встреча с Иваном'])
        self.assertTrue(result['items'][0]['start_at'].startswith('2026-10-31T10:00'))

    def test_search_by_title_and_day(self):
        phrase = 'найди встречу с иваном на завтра'
        ai, repo, _ = make({phrase: {'action': 'search', 'query': 'встреча с иваном завтра'}})
        seed(repo)
        result = ai.handle_message(phrase)
        self.assertEqual(len(result['items']), 2)

    def test_empty_search_says_what_was_searched(self):
        phrase = 'найди задачу про бассейн'
        ai, repo, _ = make({phrase: {'action': 'search', 'query': 'бассейн'}})
        seed(repo)
        result = ai.handle_message(phrase)
        self.assertNotIn('период', result['display_text'])
        self.assertIn('бассейн', result['display_text'])


class DeleteTargetTests(unittest.TestCase):
    def test_delete_with_spoken_hour(self):
        phrase = 'удали встречу с иваном завтра в 10 часов'
        ai, repo, _ = make({phrase: {'action': 'delete', 'reference': 'встреча с иваном завтра в 10 часов'}})
        seed(repo)
        result = ai.handle_message(phrase)
        self.assertEqual(result['status'], 'confirmation_required', result['display_text'])
        self.assertTrue(result['item']['start_at'].startswith('2026-10-31T10:00'))

    def test_bare_delete_after_cancel_reuses_target(self):
        phrase = 'удали встречу с иваном завтра в 10:00'
        ai, repo, planner = make({phrase: {'action': 'delete', 'reference': 'встреча с иваном'},
                                  'удали': {'action': 'delete'}})
        seed(repo)
        self.assertEqual(ai.handle_message(phrase)['status'], 'confirmation_required')
        self.assertEqual(ai.handle_message('нет')['status'], 'cancelled')
        again = ai.handle_message('удали')
        self.assertEqual(again['status'], 'confirmation_required', again['display_text'])
        self.assertEqual(again['item']['title'], 'Встреча с Иваном')

    def test_reschedule_without_title_uses_last_item(self):
        create = 'Добавь встречу с Анной завтра в 15:00'
        move = 'перенеси на послезавтра'
        ai, repo, _ = make({create: {'action': 'create', 'kind': 'meeting', 'title': 'Встреча с Анной',
                                     'temporal_text': 'завтра в 15:00'},
                            move: {'action': 'update', 'temporal_text': 'на послезавтра'}})
        self.assertEqual(ai.handle_message(create)['status'], 'success')
        moved = ai.handle_message(move)
        self.assertEqual(moved['status'], 'success', moved['display_text'])
        self.assertEqual(moved['item']['start_at'], f'2026-11-01T15:00:00{TZ}')

    def test_clear_everything_explains_the_safe_way(self):
        ai, repo, planner = make()
        seed(repo)
        result = ai.handle_message('очисти весь список записей')
        self.assertEqual(result['status'], 'clarification')
        self.assertIn('за один день', result['display_text'])
        self.assertTrue(result.get('suggestions'))
        self.assertEqual(planner.calls, [])
        self.assertEqual(len(repo._scoped('demo')), 4)


class ListAtTimeTests(unittest.TestCase):
    def test_list_at_exact_time(self):
        phrase = 'завтра в 10:00'
        ai, repo, _ = make({phrase: {'action': 'list', 'temporal_text': 'завтра в 10:00'}})
        seed(repo)
        result = ai.handle_message(phrase)
        self.assertEqual(sorted(x['title'] for x in result['items']),
                         ['Встреча с Иваном', 'Позвонить Ивану'])


class KindGuessTests(unittest.TestCase):
    def test_errand_is_a_task_not_a_meeting(self):
        phrase = 'Купить подарок маме в пятницу'
        ai, repo, _ = make({phrase: {'action': 'create', 'kind': 'meeting',
                                     'title': 'Купить подарок маме', 'temporal_text': 'в пятницу'}})
        result = ai.handle_message(phrase)
        self.assertEqual(result['status'], 'success', result['display_text'])
        self.assertEqual(result['item']['type'], 'task')

    def test_meeting_with_person_stays_a_meeting(self):
        phrase = 'Обед с Олегом в пятницу в 13:00'
        ai, repo, _ = make({phrase: {'action': 'create', 'kind': 'meeting',
                                     'title': 'Обед с Олегом', 'temporal_text': 'в пятницу в 13:00'}})
        result = ai.handle_message(phrase)
        self.assertEqual(result['item']['type'], 'meeting')


class SuggestionTests(unittest.TestCase):
    def test_morning_or_evening_has_buttons(self):
        phrase = 'Поставь встречу с Иваном завтра в 7'
        ai, repo, _ = make({phrase: {'action': 'create', 'kind': 'meeting', 'title': 'Встреча с Иваном',
                                     'temporal_text': 'завтра в 7'}})
        result = ai.handle_message(phrase)
        self.assertEqual(result['status'], 'clarification')
        self.assertEqual(result.get('suggestions'), ['Утра', 'Вечера'])

    def test_meeting_time_question_has_buttons(self):
        phrase = 'Поставь встречу с Иваном завтра'
        ai, repo, _ = make({phrase: {'action': 'create', 'kind': 'meeting', 'title': 'Встреча с Иваном',
                                     'temporal_text': 'завтра'}})
        result = ai.handle_message(phrase)
        self.assertTrue(result.get('suggestions'))
        done = ai.handle_message(result['suggestions'][0])
        self.assertEqual(done['status'], 'success', done['display_text'])


if __name__ == '__main__':
    unittest.main()
