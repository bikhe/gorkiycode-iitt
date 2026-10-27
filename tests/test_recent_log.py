"""Regression cases from the real GigaChat conversation of 2026-10-26."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from assistant_ai import AssistantAI, DemoRepository
from assistant_ai.dates import DateResolver
from assistant_ai.service import _validate_recurrence

NOW=datetime(2026,10,26,19,6,tzinfo=ZoneInfo('Europe/Moscow'))

class StubPlanner:
    def __init__(self, mapping): self.mapping=mapping
    def plan(self,text,context,now,catalog):
        return self.mapping.get(text,{'action':'unknown'})

class RealLogRegressions(unittest.TestCase):
    def test_colloquial_half_past_eleven(self):
        parser=DateResolver()
        self.assertEqual(parser.point('на 2 ноября в пол одинадцатого',NOW),'2026-11-02T10:30:00+03:00')
        self.assertEqual(parser.point('на 2 ноября в пол одиннадцатого',NOW),'2026-11-02T10:30:00+03:00')
    def test_clock_not_an_invalid_date(self):
        parser=DateResolver()
        self.assertEqual(parser.point('на 2 ноября в 11.30',NOW),'2026-11-02T11:30:00+03:00')
        self.assertEqual(parser.clock('11.30',short=True),(11,30))
        self.assertEqual(parser.clock('11 30',short=True),(11,30))
    def test_recurring_nullable_fields(self):
        x=_validate_recurrence({'freq':'weekly','interval':1,'by_weekday':[],
                                'until':'null','count':'null','bymonthday':'null'})
        self.assertIsNone(x['until'])
        self.assertIsNone(x['count'])
    def test_weekday_missing_asks_then_continues(self):
        text='Каждую неделю в 20:00 назначь встречу с командой'
        plan={'action':'create','kind':'meeting','title':'Встреча с командой',
              'temporal_text':'каждую неделю в 20:00','recurrence':{
                'freq':'weekly','interval':1,'by_weekday':[],
                'until':'null','count':'null','bymonthday':'null'}}
        repo=DemoRepository()
        ai=AssistantAI(StubPlanner({text:plan}),repo,now_func=lambda:NOW)
        first=ai.handle_message(text)
        self.assertEqual(first['status'],'clarification')
        self.assertIn('день недели',first['display_text'])
        self.assertEqual(repo.items,[])
        second=ai.handle_message('по четвергам')
        self.assertEqual(second['status'],'success')
        self.assertEqual(second['item']['start_at'],'2026-10-29T20:00:00+03:00')
        self.assertEqual(second['item']['recurrence']['by_weekday'],[3])
    def test_half_eleven_conflicts_with_weekly(self):
        a='Каждый понедельник в 10:00 назначь встречу с командой'
        b='Поставь встречу с преподавателем на 2 ноября в пол одинадцатого'
        mapping={a:{'action':'create','kind':'meeting','title':'Команда','temporal_text':'каждый понедельник в 10:00'},
                 b:{'action':'create','kind':'meeting','title':'Преподаватель','temporal_text':'на 2 ноября в пол одинадцатого'}}
        repo=DemoRepository()
        ai=AssistantAI(StubPlanner(mapping),repo,now_func=lambda:NOW)
        self.assertEqual(ai.handle_message(a)['status'],'success')
        result=ai.handle_message(b)
        self.assertEqual(result['status'],'confirmation_required')
        self.assertEqual(len(repo.items),1)
    def test_pending_time_with_dot_and_space(self):
        original='Поставь встречу с преподавателем на 2 ноября'
        plan={'action':'create','kind':'meeting','title':'Встреча с преподавателем','temporal_text':'на 2 ноября'}
        for answer in ('11.30','11 30','в пол одинадцатого'):
            with self.subTest(answer=answer):
                repo=DemoRepository()
                ai=AssistantAI(StubPlanner({original:plan}),repo,now_func=lambda:NOW)
                first=ai.handle_message(original)
                self.assertEqual(first['status'],'clarification')
                second=ai.handle_message(answer)
                self.assertEqual(second['status'],'success')
                self.assertEqual(second['item']['start_at'],
                    '2026-11-02T10:30:00+03:00' if 'пол' in answer else '2026-11-02T11:30:00+03:00')

if __name__=='__main__':unittest.main()

class PlannerOmissionRegression(unittest.TestCase):
    def test_model_drops_date_but_raw_text_is_used(self):
        text='Поставь встречу с преподавателем на 2 ноября в пол одинадцатого'
        # Model supplies only a fragment; user request contains both date and time.
        plan={'action':'create','kind':'meeting','title':'Преподаватель',
              'temporal_text':'в пол одинадцатого'}
        repo=DemoRepository()
        ai=AssistantAI(StubPlanner({text:plan}),repo,now_func=lambda:NOW)
        result=ai.handle_message(text)
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['item']['start_at'],'2026-11-02T10:30:00+03:00')
