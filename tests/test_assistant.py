import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from assistant_ai import AssistantAI, DemoRepository
from assistant_ai.dates import DateResolver, DateError

NOW=datetime(2026,10,26,19,6,tzinfo=ZoneInfo('Europe/Moscow'))

class FakePlanner:
    def __init__(self,mapping):self.mapping=mapping;self.calls=[]
    def plan(self,text,context,now,catalog):
        self.calls.append(text)
        value=self.mapping.get(text)
        if value is None:return {'action':'unknown'}
        return value

class AssistantTests(unittest.TestCase):
    def make(self,plans=None):
        repo=DemoRepository();planner=FakePlanner(plans or {})
        ai=AssistantAI(planner,repo,now_func=lambda:NOW)
        return ai,repo,planner
    def test_date_verification(self):
        parser=DateResolver()
        self.assertEqual(parser.point('через 15 дней',NOW),'2026-11-10')
        self.assertEqual(parser.point('до конца недели',NOW),'2026-11-01')
        self.assertEqual(parser.point('31 октября в 09:00',NOW),'2026-10-31T09:00:00+03:00')
        self.assertEqual(parser.point('завтра в 15:00',NOW),'2026-10-27T15:00:00+03:00')
        self.assertEqual(parser.point('5 числа моего любимого месяца',NOW,favorite_month=10),'2027-10-05')
        self.assertEqual(parser.point('на следующей пятнице',NOW),'2026-11-06')
        with self.assertRaises(DateError):parser.point('на 31 ноября',NOW)
        with self.assertRaises(DateError):parser.point('через квартиль',NOW)
        with self.assertRaises(DateError):parser.point('завтра в 10',NOW)
    def test_ask_favorite_then_create_reminder(self):
        p={'Напомни пятого числа моего любимого месяца поздравить подругу':{'action':'create','kind':'task','title':'поздравить подругу','temporal_text':'пятого числа моего любимого месяца'}}
        ai,repo,planner=self.make(p)
        first=ai.handle_message(list(p)[0]);self.assertEqual(first['status'],'clarification')
        self.assertIn('месяц',first['display_text'])
        second=ai.handle_message('октябрь');self.assertEqual(second['status'],'success')
        item=second['item'];self.assertEqual(item['type'],'reminder');self.assertEqual(item['title'],'поздравить подругу');self.assertEqual(item['due_at'],'2027-10-05')
        self.assertEqual(len(planner.calls),1)
    def test_reminder_quartile_then_15_days(self):
        phrase='Напомни поздравить бабушку с днем рождения через квартиль'
        ai,repo,planner=self.make({phrase:{'action':'create','kind':'reminder','title':'поздравить бабушку с днем рождения','temporal_text':'через квартиль'}})
        self.assertEqual(ai.handle_message(phrase)['status'],'clarification')
        created=ai.handle_message('через 15 дней');self.assertEqual(created['status'],'success');self.assertEqual(created['item']['due_at'],'2026-11-10')
    def test_context_meeting(self):
        phrase='Поставь встречу завтра'
        ai,repo,p=self.make({phrase:{'action':'create','kind':'meeting','title':'Встреча','temporal_text':'завтра'}})
        self.assertEqual(ai.handle_message(phrase)['status'],'clarification')
        result=ai.handle_message('в 15:00')
        self.assertEqual(result['status'],'success');self.assertEqual(result['item']['start_at'],'2026-10-27T15:00:00+03:00')
    def test_meeting_conflicts_and_yes(self):
        a='Поставь встречу завтра в 15:00';b='Поставь еще одну встречу завтра в 15:30'
        plans={a:{'action':'create','kind':'meeting','title':'Встреча','temporal_text':'завтра в 15:00'},
               b:{'action':'create','kind':'meeting','title':'Встреча','temporal_text':'завтра в 15:30'}}
        ai,repo,p=self.make(plans)
        self.assertEqual(ai.handle_message(a)['status'],'success')
        self.assertEqual(ai.handle_message(b)['status'],'confirmation_required')
        self.assertEqual(len(repo.items),1)
        self.assertEqual(ai.handle_message('да')['status'],'success')
        self.assertEqual(len(repo.items),2)
        self.assertNotIn('#',ai.handle_message('да')['speech_text'])
    def test_delete_exact_1500_not_1530(self):
        phrase='Убери встречу на завтра которая в 15:00'
        ai,repo,p=self.make({phrase:{'action':'delete','kind':'meeting','reference':'встречу'}})
        one=repo.create_item('demo',{'type':'meeting','title':'Встреча 15','start_at':'2026-10-27T15:00:00+03:00','due_at':None,'duration_minutes':60,'status':'pending','recurrence':None},'a')
        two=repo.create_item('demo',{'type':'meeting','title':'Встреча 1530','start_at':'2026-10-27T15:30:00+03:00','due_at':None,'duration_minutes':60,'status':'pending','recurrence':None},'b')
        result=ai.handle_message(phrase)
        self.assertEqual(result['status'],'confirmation_required');self.assertEqual(result['item']['id'],one['id'])
        done=ai.handle_message('да');self.assertEqual(done['status'],'success')
        self.assertIsNone(repo.get_item('demo',one['id']));self.assertIsNotNone(repo.get_item('demo',two['id']))
    def test_delete_ambiguous_hour_requires_clarification(self):
        phrase='Убери встречу на завтра которая в три часа'
        ai,repo,p=self.make({phrase:{'action':'delete','kind':'meeting','reference':'встречу'}})
        repo.create_item('demo',{'type':'meeting','title':'Встреча','start_at':'2026-10-27T15:00:00+03:00','due_at':None,'duration_minutes':60,'status':'pending','recurrence':None},'a')
        result=ai.handle_message(phrase)
        self.assertEqual(result['status'],'clarification');self.assertEqual(len(repo.items),1)
    def test_monday_recurs_from_next_week(self):
        phrase='Каждый понедельник в 10:00 назначь встречу с командой'
        plan={'action':'create','kind':'meeting','title':'Встреча с командой','temporal_text':'каждый понедельник в 10:00'}
        ai,repo,p=self.make({phrase:plan});result=ai.handle_message(phrase)
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['item']['start_at'],'2026-11-02T10:00:00+03:00')
        self.assertEqual(result['item']['recurrence']['by_weekday'],[0])
    def test_period_list(self):
        phrase='Что у меня в ближайшие три дня?'
        ai,repo,p=self.make({phrase:{'action':'list','kind':'any'}})
        repo.create_item('demo',{'type':'meeting','title':'Встреча','start_at':'2026-10-27T15:00:00+03:00','due_at':None,'recurrence':None,'duration_minutes':60},'a')
        result=ai.handle_message(phrase)
        self.assertEqual(result['status'],'success');self.assertEqual(len(result['items']),1)
    def test_search_conserves_context(self):
        phrase='Найди задачу по договору'
        ai,repo,p=self.make({phrase:{'action':'search','kind':'task','query':'договор'}})
        doc=repo.create_item('demo',{'type':'task','title':'Подписать контракт','due_at':None,'start_at':None,'recurrence':None,'duration_minutes':60},'a')
        repo.create_item('demo',{'type':'task','title':'Подготовить презентацию','due_at':None,'start_at':None,'recurrence':None,'duration_minutes':60},'b')
        result=ai.handle_message(phrase);self.assertEqual(len(result['items']),1);self.assertEqual(result['items'][0]['id'],doc['id'])
        self.assertEqual(repo.get_session('demo','default')['last_item_id'],doc['id'])
    def test_idempotent_repository(self):
        _,repo,_=self.make();data={'type':'task','title':'Тест','due_at':'2026-11-01','start_at':None,'recurrence':None}
        a=repo.create_item('demo',data,'same');b=repo.create_item('demo',data,'same')
        self.assertEqual(a['id'],b['id']);self.assertEqual(len(repo.items),1)
    def test_user_scope(self):
        ai,repo,p=self.make()
        task=repo.create_item('A',{'type':'task','title':'Секрет','due_at':None,'start_at':None,'recurrence':None},'a')
        self.assertIsNone(repo.get_item('B',task['id']))
    def test_negation_no_write(self):
        phrase='Не создавай напоминание, скажи дату завтра'
        ai,repo,p=self.make({phrase:{'action':'create','kind':'reminder','title':'Напомнить','negated':True}})
        self.assertEqual(ai.handle_message(phrase)['status'],'success');self.assertEqual(len(repo.items),0)
    def test_gigachat_down_no_false_success(self):
        class Broken:
            def plan(self,*args):raise TimeoutError('no connection')
        repo=DemoRepository();ai=AssistantAI(Broken(),repo,now_func=lambda:NOW)
        result=ai.handle_message('Поставь встречу завтра в 15:00')
        self.assertEqual(result['status'],'error');self.assertFalse(repo.items)

if __name__=='__main__':unittest.main()

class ExtraTests(unittest.TestCase):
    def make(self,plans=None):
        repo=DemoRepository();ai=AssistantAI(FakePlanner(plans or {}),repo,now_func=lambda:NOW);return ai,repo

    def test_repeating_event_visible_next_week_not_today(self):
        creation='Каждый понедельник в 10:00 назначь встречу с командой'
        listing='Что у меня на следующей неделе?'
        ai,repo=self.make({creation:{'action':'create','kind':'meeting','title':'Встреча с командой','temporal_text':'каждый понедельник в 10:00'},listing:{'action':'list','kind':'meeting'}})
        self.assertEqual(ai.handle_message(creation)['status'],'success')
        result=ai.handle_message(listing)
        self.assertEqual(result['status'],'success');self.assertEqual(len(result['items']),1)
        self.assertTrue(result['items'][0]['occurrence_at'].startswith('2026-11-02T10:00'))

    def test_new_meeting_conflicts_with_weekly(self):
        weekly='Каждый понедельник в 10:00 назначь встречу с командой'
        separate='Назначь встречу с учителем в следующий понедельник в 10:30'
        ai,repo=self.make({weekly:{'action':'create','kind':'meeting','title':'Команда','temporal_text':'каждый понедельник в 10:00'},separate:{'action':'create','kind':'meeting','title':'Учитель','temporal_text':'следующий понедельник в 10:30'}})
        self.assertEqual(ai.handle_message(weekly)['status'],'success')
        self.assertEqual(ai.handle_message(separate)['status'],'confirmation_required')
        self.assertEqual(len(repo.items),1)

    def test_duplicate_not_silently_created(self):
        command='Поставь встречу завтра в 15:00'
        ai,repo=self.make({command:{'action':'create','kind':'meeting','title':'Встреча','temporal_text':'завтра в 15:00'}})
        self.assertEqual(ai.handle_message(command)['status'],'success')
        self.assertEqual(ai.handle_message(command)['status'],'confirmation_required')
        self.assertEqual(len(repo.items),1)

    def test_update_after_search_without_due_date_as_clarification(self):
        search='Найди задачу по договору';change='Перенеси её на 16:00'
        ai,repo=self.make({search:{'action':'search','query':'договор'},change:{'action':'update','reference':'её','temporal_text':'на 16:00'}})
        task=repo.create_item('demo',{'type':'task','title':'Подписать контракт','due_at':None,'start_at':None,'recurrence':None,'duration_minutes':60},'x')
        self.assertEqual(ai.handle_message(search)['status'],'success')
        self.assertEqual(ai.handle_message(change)['status'],'clarification')
        self.assertIsNone(repo.get_item('demo',task['id'])['due_at'])

    def test_update_meeting_with_reference(self):
        create='Поставь встречу завтра в 15:00';move='Перенеси её на завтра в 16:00'
        ai,repo=self.make({create:{'action':'create','kind':'meeting','title':'Встреча','temporal_text':'завтра в 15:00'},move:{'action':'update','reference':'её','temporal_text':'завтра в 16:00'}})
        item=ai.handle_message(create)['item'];self.assertEqual(ai.handle_message(move)['status'],'success')
        self.assertEqual(repo.get_item('demo',item['id'])['start_at'],'2026-10-27T16:00:00+03:00')

    def test_without_due_not_shown_as_upcoming(self):
        listing='Что у меня на следующей неделе?'
        ai,repo=self.make({listing:{'action':'list','kind':'task'}})
        repo.create_item('demo',{'type':'task','title':'Нет срока','due_at':None,'start_at':None,'recurrence':None,'duration_minutes':60},'x')
        self.assertEqual(ai.handle_message(listing)['items'],[])
