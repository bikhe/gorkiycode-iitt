"""Granular regression suite: one distinct unittest per speech/date case.
All deterministic; does not pretend to test live GigaChat network.
"""
import unittest
import tempfile
from pathlib import Path
from datetime import datetime,date
from zoneinfo import ZoneInfo
from assistant_ai.dates import DateResolver,DateError,month_from_text
from assistant_ai.service import _recurrence_from_text,_validate_recurrence
from assistant_ai.timing import duration_minutes,numeric_range
from assistant_ai.title_helpers import fallback_title
from assistant_ai.search_helpers import score
from assistant_ai import AssistantAI,DemoRepository,SQLiteRepository
from assistant_ai.rule_planner import RulePlanner
from tests.test_spoken_time_matrix import CLOCK_CASES, SHORT_CASES

NOW=datetime(2026,10,26,14,0,tzinfo=ZoneInfo('Europe/Moscow'))
P=DateResolver()

def spawn(name,case,fn,cls):
    def method(self):fn(self,case)
    setattr(cls,'test_'+name,method)

class ClockUnitMatrix(unittest.TestCase):pass
class CalendarPointMatrix(unittest.TestCase):pass
class SpokenShortMatrix(unittest.TestCase):pass
class SemanticSearchMatrix(unittest.TestCase):pass

for idx,(phrase,expected) in enumerate(CLOCK_CASES.items(),1):
    spawn(f'clock_{idx:03d}',(phrase,expected),lambda self,c:self.assertEqual(P.clock(c[0]),c[1]),ClockUnitMatrix)
    spawn(f'datetime_{idx:03d}',(phrase,expected),lambda self,c:self.assertEqual(
        tuple(map(int,P.point('2 ноября '+c[0],NOW)[11:16].split(':'))),c[1]),CalendarPointMatrix)
for idx,(phrase,expected) in enumerate(SHORT_CASES.items(),1):
    spawn(f'answer_{idx:03d}',(phrase,expected),lambda self,c:self.assertEqual(P.clock(c[0],short=True),c[1]),SpokenShortMatrix)

SEMANTIC_PAIRS=[
 ('договор','подписать контракт',True),('презентация','подготовить слайды',True),
 ('почта','проверить Gmail',True),('имейл','отправить письмо',True),
 ('позвонить','набрать по телефону',True),('созвон','совещание',True),
 ('купить','заказать билеты',True),('договор','нарисовать пейзаж',False),
 ('машина','презентация кафедра',False),('список покупок','отправить доклад',False),
 ('фонари','встреча с Иваном',False),('поздравить','поздравить подругу',True),
]
for i,(query,title,matched) in enumerate(SEMANTIC_PAIRS,1):
    spawn(f'search_{i:03d}',(query,title,matched),lambda self,c:self.assertEqual(score(c[0],c[1])>0,c[2]),SemanticSearchMatrix)

class CalendarBoundaries(unittest.TestCase):
    def test_today(self):self.assertEqual(P.point('сегодня',NOW),'2026-10-26')
    def test_tomorrow(self):self.assertEqual(P.point('завтра',NOW),'2026-10-27')
    def test_after_tomorrow(self):self.assertEqual(P.point('послезавтра',NOW),'2026-10-28')
    def test_next_monday(self):self.assertEqual(P.point('следующий понедельник',NOW),'2026-11-02')
    def test_next_friday(self):self.assertEqual(P.point('следующая пятница',NOW),'2026-11-06')
    def test_month_named(self):self.assertEqual(P.point('5 декабря',NOW),'2026-12-05')
    def test_month_rollover(self):self.assertEqual(P.point('5 октября',NOW),'2027-10-05')
    def test_correct_dotted_date(self):self.assertEqual(P.point('на 11.11',NOW),'2026-11-11')
    def test_dotted_clock_over_date(self):self.assertEqual(P.point('завтра в 11.11',NOW),'2026-10-27T11:11:00+03:00')
    def test_tomorrow_13_hours(self):self.assertEqual(P.point('завтра в час дня',NOW),'2026-10-27T13:00:00+03:00')
    def test_no_bogus_may_from_mama(self):self.assertIsNone(month_from_text('мама и машина'))
    def test_may_still_works(self):self.assertEqual(month_from_text('5 мая'),5)
    def test_monthly_invalid_31_september(self):
        with self.assertRaises(DateError):P.point('31 сентября',NOW)
    def test_bad_time_with_date(self):
        with self.assertRaises(DateError):P.point('завтра в 25:61',NOW)
    def test_daypart_without_time(self):
        with self.assertRaises(DateError):P.point('завтра вечером',NOW)
    def test_ambiguous_11_proper_question(self):
        with self.assertRaises(DateError) as e:P.point('в четверг в 11',NOW)
        self.assertIn('11',str(e.exception));self.assertNotIn('10 утра',str(e.exception))
    def test_relative_two_hours(self):self.assertEqual(P.point('через два часа',NOW),'2026-10-26T16:00:00+03:00')
    def test_relative_half_hour(self):self.assertEqual(P.point('через полчаса',NOW),'2026-10-26T14:30:00+03:00')
    def test_relative_hour_twenty(self):self.assertEqual(P.point('через час двадцать',NOW),'2026-10-26T15:20:00+03:00')
    def test_relative_two_and_half(self):self.assertEqual(P.point('через два с половиной часа',NOW),'2026-10-26T16:30:00+03:00')
    def test_relative_one_and_half(self):self.assertEqual(P.point('через полтора часа',NOW),'2026-10-26T15:30:00+03:00')
    def test_relative_2h30(self):self.assertEqual(P.point('через 2 часа 30 минут',NOW),'2026-10-26T16:30:00+03:00')
    def test_date_vs_clock_11_30(self):self.assertEqual(P.point('2 ноября в 11.30',NOW),'2026-11-02T11:30:00+03:00')
    def test_favorite_month(self):self.assertEqual(P.point('5 числа моего любимого месяца',NOW,12),'2026-12-05')
    def test_interval_this_week(self):self.assertEqual(P.interval('на этой неделе',NOW),(date(2026,10,26),date(2026,11,1)))
    def test_interval_next_week(self):self.assertEqual(P.interval('на следующей неделе',NOW),(date(2026,11,2),date(2026,11,8)))
    def test_interval_weekends(self):self.assertEqual(P.interval('на выходных',NOW),(date(2026,10,31),date(2026,11,1)))

class MeetingDurationMatrix(unittest.TestCase):
    def test_range(self):self.assertEqual(numeric_range('Завтра с 15:00 до 16:30')[1],90)
    def test_duration_2h(self):self.assertEqual(duration_minutes('Встреча на 2 часа',None),120)
    def test_duration_90min(self):self.assertEqual(duration_minutes('Встреча на 90 минут',None),90)
    def test_duration_half(self):self.assertEqual(duration_minutes('Встреча на полтора часа',None),90)
    def test_duration_default(self):self.assertEqual(duration_minutes('Назначь встречу',None),60)
    def test_duration_from_llm(self):self.assertEqual(duration_minutes('Назначь встречу','75'),75)
    def test_bad_duration(self):
        with self.assertRaises(ValueError):duration_minutes('Встреча на 0 часов',0)
    def test_bad_range(self):
        with self.assertRaises(ValueError):numeric_range('Встреча с 18:00 до 17:00')

class TitleMatrix(unittest.TestCase):
    def test_remind_morning(self):self.assertEqual(fallback_title('Напомни завтра в 10 утра позвонить Ивану','reminder'),'Позвонить Ивану')
    def test_remind_month(self):self.assertEqual(fallback_title('Напомни пятого числа моего любимого месяца поздравить подругу','reminder'),'Поздравить подругу')
    def test_meeting_range(self):self.assertEqual(fallback_title('Поставь встречу с Иваном завтра с 15:00 до 16:30','meeting'),'Встреча с Иваном')
    def test_meeting_weekly(self):self.assertEqual(fallback_title('Каждый понедельник в 10:00 назначь встречу с командой','meeting'),'Встреча с командой')
    def test_task_friday(self):self.assertEqual(fallback_title('В пятницу нужно отправить отчёт','task'),'Отправить отчёт')
    def test_task_deadline(self):self.assertEqual(fallback_title('Поставь задачу купить билеты до конца недели','task'),'Купить билеты')
    def test_reminder_tomorrow(self):self.assertEqual(fallback_title('Напомни завтра купить хлеб','reminder'),'Купить хлеб')

class PersistentStoreCases(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=str(Path(self.temp.name)/'data.sqlite3')
        self.db=SQLiteRepository(self.path)
        self.event={'type':'meeting','title':'Звонок с командой','start_at':'2026-10-27T10:00:00+03:00','due_at':None,'recurrence':None}
    def tearDown(self):self.db.close();self.temp.cleanup()
    def test_write_read(self):
        r=self.db.create_item('u1',self.event,'call1');self.assertEqual(self.db.get_item('u1',r['id'])['title'],self.event['title'])
    def test_two_users(self):
        r=self.db.create_item('u1',self.event,'call1');self.assertIsNone(self.db.get_item('u2',r['id']))
    def test_search_scoped(self):
        self.db.create_item('u1',self.event,'call1');self.assertEqual(self.db.search_items('u2','команда'),[])
    def test_idempotent(self):
        a=self.db.create_item('u1',self.event,'call1');b=self.db.create_item('u1',self.event,'call1');self.assertEqual(a['id'],b['id'])
    def test_across_restart(self):
        saved=self.db.create_item('u1',self.event,'call1');self.db.close();self.db=SQLiteRepository(self.path)
        self.assertEqual(self.db.get_item('u1',saved['id'])['title'],self.event['title'])
    def test_session_across_restart(self):
        self.db.save_session('u1','s1',{'pending':{'need':'time'}});self.db.close();self.db=SQLiteRepository(self.path)
        self.assertEqual(self.db.get_session('u1','s1')['pending']['need'],'time')
    def test_soft_delete(self):
        a=self.db.create_item('u1',self.event,'call1');self.assertTrue(self.db.delete_item('u1',a['id']));self.assertIsNone(self.db.get_item('u1',a['id']))
    def test_cannot_delete_other(self):
        a=self.db.create_item('u1',self.event,'call1');self.assertFalse(self.db.delete_item('u2',a['id']))
    def test_cannot_update_other(self):
        a=self.db.create_item('u1',self.event,'call1')
        with self.assertRaises(ValueError):self.db.update_item('u2',a['id'],{'title':'Подделка'})
    def test_cannot_update_user_id(self):
        a=self.db.create_item('u1',self.event,'call1')
        with self.assertRaises(ValueError):self.db.update_item('u1',a['id'],{'user_id':'u2'})
    def test_persist_recurrence(self):
        a=self.db.create_item('u1',{**self.event,'recurrence':{'freq':'weekly','interval':1,'by_weekday':[1]}},'rid')
        self.db.close();self.db=SQLiteRepository(self.path);self.assertEqual(self.db.get_item('u1',a['id'])['recurrence']['by_weekday'],[1])
    def test_exchange_idempotency(self):
        result={'status':'success','display_text':'Готово'}
        self.db.save_exchange('u1','s1','voice1',result)
        self.assertEqual(self.db.get_exchange('u1','s1','voice1'),result)
        self.assertIsNone(self.db.get_exchange('u2','s1','voice1'))

class OfflineEndToEnd(unittest.TestCase):
    def setUp(self):
        self.db=DemoRepository();self.app=AssistantAI(RulePlanner(),self.db,now_func=lambda:NOW)
    def test_create_meeting(self):
        r=self.app.handle_message('Добавь встречу с Иваном завтра в 15:00')
        self.assertEqual(r['status'],'success');self.assertEqual(r['item']['start_at'],'2026-10-27T15:00:00+03:00')
    def test_reminder_creation(self):
        r=self.app.handle_message('Напомни завтра в 10 утра позвонить Ивану')
        self.assertEqual(r['item']['title'],'Позвонить Ивану')
    def test_range_duration_end_to_end(self):
        r=self.app.handle_message('Поставь встречу с Иваном завтра с 15:00 до 16:30')
        self.assertEqual(r['item']['duration_minutes'],90)
    def test_list_after_create(self):
        self.app.handle_message('Напомни завтра в 10 утра позвонить Ивану')
        result=self.app.handle_message('Что у меня завтра?');self.assertEqual(len(result['items']),1)
    def test_search_after_create(self):
        self.app.handle_message('Добавь встречу с Иваном завтра в 15:00')
        result=self.app.handle_message('Найди встречу про Ивана');self.assertEqual(len(result['items']),1)
    def test_edit_after_search(self):
        self.app.handle_message('Добавь встречу с Иваном завтра в 15:00')
        self.app.handle_message('Найди встречу про Ивана')
        result=self.app.handle_message('Перенеси её на завтра в 16:00');self.assertEqual(result['status'],'success')
    def test_duplicate_requires_confirmation(self):
        self.app.handle_message('Добавь встречу с Иваном завтра в 15:00')
        x=self.app.handle_message('Добавь встречу с Иваном завтра в 15:00')
        self.assertEqual(x['status'],'confirmation_required')
    def test_weekly_starts_correct_monday(self):
        r=self.app.handle_message('Каждый понедельник в 10:00 назначь встречу с командой')
        self.assertEqual(r['item']['start_at'],'2026-11-02T10:00:00+03:00')
    def test_weekly_list(self):
        self.app.handle_message('Каждый понедельник в 10:00 назначь встречу с командой')
        r=self.app.handle_message('Что у меня на следующей неделе?');self.assertEqual(len(r['items']),1)
    def test_conflict(self):
        self.app.handle_message('Каждый понедельник в 10:00 назначь встречу с командой')
        r=self.app.handle_message('Добавь встречу с Иваном 2 ноября в 10:30')
        self.assertEqual(r['status'],'confirmation_required')
    def test_negation_prevents_write(self):
        r=self.app.handle_message('Не создавай напоминание, просто спроси меня')
        self.assertEqual(self.db.items,[])
    def test_natural_date_without_clock(self):
        r=self.app.handle_message('Напомни завтра купить хлеб');self.assertEqual(r['item']['due_at'],'2026-10-27')
        out=self.app.handle_message('Что у меня завтра?')['display_text']
        self.assertIn('время не указано',out)

if __name__=='__main__':unittest.main()
