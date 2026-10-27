"""Wide deterministic regression matrix for colloquial Russian time.
This suite does not call GigaChat; real API still requires separate testing.
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from assistant_ai.dates import DateResolver, DateError
from assistant_ai import AssistantAI, DemoRepository

NOW=datetime(2026,10,26,19,6,tzinfo=ZoneInfo('Europe/Moscow'))
P=DateResolver('Europe/Moscow')

class StubPlanner:
    def plan(self,text,state,now,catalog):
        if 'создай встречу' in text.casefold() or 'поставь встречу' in text.casefold():
            return {'action':'create','kind':'meeting','title':'Тестовая встреча','temporal_text':text}
        if 'убери встречу' in text.casefold():
            return {'action':'delete','reference':'встречу'}
        return {'action':'unknown'}

# Inputs the assistant should normalize to 24-hour time.
CLOCK_CASES = {
    'в пол одиннадцатого вечера': (22,30),
    'в пол одинадцатого вечера': (22,30),
    'в пол одинацатого вечера': (22,30),
    'пол одиннадцатого вечера': (22,30),
    'в половине одиннадцатого вечера': (22,30),
    'в половину одиннадцатого вечера': (22,30),
    'в пол одиннадцатого утра': (10,30),
    'в пол первого дня': (12,30),
    'в пол первого ночи': (0,30),
    'в пол пятого вечера': (16,30),
    'в пол двенадцатого ночи': (23,30),
    'в четверть одиннадцатого вечера': (22,15),
    'в четверть одиннадцатого утра': (10,15),
    'в четверть двенадцатого ночи': (23,15),
    'без четверти одиннадцать вечера': (22,45),
    'без пятнадцати одиннадцать вечера': (22,45),
    'без десяти одиннадцать вечера': (22,50),
    'без пяти одиннадцать утра': (10,55),
    'без двадцати двенадцать дня': (11,40),
    'в десять тридцать вечера': (22,30),
    'в одиннадцать тридцать вечера': (23,30),
    'в двадцать два часа тридцать минут': (22,30),
    'в три часа дня': (15,0),
    'в три часа ночи': (3,0),
    'в десять вечера': (22,0),
    'в десять утра': (10,0),
    'в полночь': (0,0),
    'в полдень': (12,0),
    'в 22:30': (22,30),
    'в 22.30': (22,30),
    'в 22 30': (22,30),
    'в 11:30 вечера': (23,30),
    'в 11.30 вечера': (23,30),
    'в 11:30 утра': (11,30),
    'в 23:00': (23,0),
    'в 05:05': (5,5),
    'в двадцать три тридцать': (23,30),
    'в десять тридцать': (10,30),
    'пол-одиннадцатого вечера': (22,30),
}
SHORT_CASES={
    '11.30':(11,30), '11 30':(11,30),
    '11:30':(11,30), 'в 11:30':(11,30),
    'в пол одиннадцатого вечера':(22,30),
    'в пол одинацатого вечера':(22,30),
    'в четверть одиннадцатого вечера':(22,15),
    'в 23:00':(23,0),
    'в полночь':(0,0),
    'в полдень':(12,0),
    'в десять тридцать вечера':(22,30),
}

class SpokenTimeMatrix(unittest.TestCase):
    def test_full_clock(self):
        for phrase, expected in CLOCK_CASES.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(P.clock(phrase),expected)
    def test_short_clock(self):
        for phrase, expected in SHORT_CASES.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(P.clock(phrase,short=True),expected)
    def test_date_plus_clock(self):
        for phrase, expected in CLOCK_CASES.items():
            with self.subTest(phrase=phrase):
                at=P.point('2 ноября '+phrase,NOW)
                stamp=datetime.fromisoformat(at)
                self.assertEqual((stamp.year,stamp.month,stamp.day),(2026,11,2))
                self.assertEqual((stamp.hour,stamp.minute),expected)
    def test_date_vs_time(self):
        self.assertEqual(P.point('на 11.11',NOW),'2026-11-11')
        self.assertEqual(P.point('на 2 ноября в 11.30',NOW),'2026-11-02T11:30:00+03:00')
        self.assertEqual(P.point('на 2 ноября в 11 30',NOW),'2026-11-02T11:30:00+03:00')
        self.assertIsNone(P.point('на 11.30',NOW))
    def test_invalid_clock(self):
        for x in ('в 25:61','в 23:70','в 24:00','в 21:75'):
            with self.subTest(x=x):
                with self.assertRaises(DateError): P.clock(x)
    def test_ambiguous_clock(self):
        for x in ('в 3','в 10','в одиннадцать'):
            with self.subTest(x=x):
                with self.assertRaises(DateError): P.clock(x)
    def test_full_context_create(self):
        for phrase,expected in [('в пол одиннадцатого вечера','2026-11-02T22:30:00+03:00'),
                                ('в пол одинадцатого утра','2026-11-02T10:30:00+03:00'),
                                ('в 23:30','2026-11-02T23:30:00+03:00')]:
            with self.subTest(phrase=phrase):
                repo=DemoRepository()
                ai=AssistantAI(StubPlanner(),repo,now_func=lambda:NOW)
                result=ai.handle_message('Создай встречу на 2 ноября '+phrase)
                self.assertEqual(result['status'],'success',result)
                self.assertEqual(result['item']['start_at'],expected)
    def test_time_follow_up(self):
        repo=DemoRepository()
        ai=AssistantAI(StubPlanner(),repo,now_func=lambda:NOW)
        q=ai.handle_message('Создай встречу 2 ноября')
        self.assertEqual(q['status'],'clarification')
        answer=ai.handle_message('пол одиннадцатого вечера')
        self.assertEqual(answer['status'],'success')
        self.assertEqual(answer['item']['start_at'],'2026-11-02T22:30:00+03:00')
    def test_delete_target_by_spoken_clock(self):
        repo=DemoRepository()
        ai=AssistantAI(StubPlanner(),repo,now_func=lambda:NOW)
        repo.create_item('demo',{'type':'meeting','title':'Вечерняя','start_at':'2026-11-02T22:30:00+03:00','due_at':None,'duration_minutes':60,'recurrence':None,'status':'pending'},'r1')
        repo.create_item('demo',{'type':'meeting','title':'Утренняя','start_at':'2026-11-02T10:30:00+03:00','due_at':None,'duration_minutes':60,'recurrence':None,'status':'pending'},'r2')
        result=ai.handle_message('Убери встречу на 2 ноября в пол одиннадцатого вечера')
        self.assertEqual(result['status'],'confirmation_required',result)
        self.assertIn('Вечерняя',result['display_text'])

if __name__=='__main__': unittest.main()

class RecurrenceAndRelativeMatrix(unittest.TestCase):
    def test_relative_time_compound(self):
        start=datetime(2026,10,26,20,12,tzinfo=ZoneInfo('Europe/Moscow'))
        self.assertEqual(P.point('через полчаса',start),'2026-10-26T20:42:00+03:00')
        self.assertEqual(P.point('через 2 часа 30 минут',start),'2026-10-26T22:42:00+03:00')
        self.assertEqual(P.point('через 15 дней',start),'2026-11-10')
        self.assertEqual(P.point('через год',start),'2027-10-26')
    def test_multiple_weekdays(self):
        from assistant_ai.service import _recurrence_from_text
        rule=_recurrence_from_text('По понедельникам и четвергам встреча')
        self.assertEqual(rule['by_weekday'],[0,3])
        self.assertEqual(rule['freq'],'weekly')
    def test_monthly_ordinal(self):
        from assistant_ai.service import _recurrence_from_text
        self.assertEqual(_recurrence_from_text('каждое пятое число месяца')['bymonthday'],5)
        self.assertEqual(_recurrence_from_text('5 числа каждого месяца')['bymonthday'],5)
    def test_monthly_date_only_end_to_end(self):
        class ReminderStub:
            def plan(self,text,*args):
                return {'action':'create','kind':'reminder','title':'Оплатить сервис','temporal_text':text}
        ai=AssistantAI(ReminderStub(),DemoRepository(),now_func=lambda:NOW)
        result=ai.handle_message('Каждое пятое число месяца напомни оплатить сервис')
        self.assertEqual(result['status'],'success',result)
        self.assertEqual(result['item']['due_at'],'2026-11-05')
        self.assertEqual(result['item']['recurrence']['bymonthday'],5)
    def test_unsupported_recurrence_never_saved(self):
        class ReminderStub:
            def plan(self,text,*args):
                return {'action':'create','kind':'reminder','title':'Оплатить сервис','temporal_text':text}
        repo=DemoRepository()
        ai=AssistantAI(ReminderStub(),repo,now_func=lambda:NOW)
        result=ai.handle_message('Каждый второй понедельник месяца напомни оплатить сервис')
        self.assertEqual(result['status'],'clarification',result)
        self.assertEqual(repo.items,[])
