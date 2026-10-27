import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from assistant_ai import AssistantAI,DemoRepository

NOW=datetime(2026,10,26,14,tzinfo=ZoneInfo('Europe/Moscow'))

class Planner:
    def plan(self,text,*args):
        if text.startswith('Удали всю серию'):return {'action':'delete','reference':'её'}
        if text.startswith('Удали'):return {'action':'delete','reference':'встречу с Иваном'}
        if text.startswith('Перенеси'):return {'action':'update','reference':'её','temporal_text':text}
        return {'action':'unknown'}

class SafeSelection(unittest.TestCase):
    def setUp(self):
        self.repo=DemoRepository();self.ai=AssistantAI(Planner(),self.repo,now_func=lambda:NOW)
        self.a=self.repo.create_item('demo',{'type':'meeting','title':'Встреча с Иваном','start_at':'2026-11-02T15:00:00+03:00','recurrence':None,'duration_minutes':60},'one')
        self.b=self.repo.create_item('demo',{'type':'meeting','title':'Встреча с Иваном','start_at':'2026-11-03T15:00:00+03:00','recurrence':None,'duration_minutes':60},'two')
    def test_ambiguous_never_deletes(self):
        out=self.ai.handle_message('Удали встречу с Иваном')
        self.assertEqual(out['status'],'clarification');self.assertEqual(len(self.repo._scoped('demo')),2)
        self.assertNotIn('#',out['speech_text'])
    def test_choice_second_then_confirm(self):
        self.ai.handle_message('Удали встречу с Иваном')
        r=self.ai.handle_message('вторую')
        self.assertEqual(r['status'],'confirmation_required')
        self.assertIn('03.11.',r['display_text'])
        self.ai.handle_message('да')
        self.assertIsNotNone(self.repo.get_item('demo',self.a['id']))
        self.assertIsNone(self.repo.get_item('demo',self.b['id']))
    def test_choice_invalid_safe(self):
        self.ai.handle_message('Удали встречу с Иваном')
        self.assertEqual(self.ai.handle_message('восьмую')['status'],'clarification')
        self.assertEqual(len(self.repo._scoped('demo')),2)
    def test_choice_cancel(self):
        self.ai.handle_message('Удали встречу с Иваном')
        self.assertEqual(self.ai.handle_message('отмена')['status'],'cancelled')
        self.assertEqual(len(self.repo._scoped('demo')),2)
    def test_no_confirmation_no_delete(self):
        self.ai.handle_message('Удали встречу с Иваном')
        self.ai.handle_message('первую')
        self.assertEqual(len(self.repo._scoped('demo')),2)

class RecurrenceProtection(unittest.TestCase):
    def setUp(self):
        self.repo=DemoRepository();self.ai=AssistantAI(Planner(),self.repo,now_func=lambda:NOW)
        self.item=self.repo.create_item('demo',{'type':'meeting','title':'Встреча с командой',
           'start_at':'2026-11-02T10:00:00+03:00','recurrence':{'freq':'weekly','interval':1,'by_weekday':[0]},'duration_minutes':60},'weekly')
        state={'last_item_id':self.item['id'],'pending':None}
        self.repo.save_session('demo','default',state)
    def test_single_deletion_not_whole_series(self):
        result=self.ai.handle_message('Удали встречу с Иваном')
        self.assertNotEqual(result['status'],'success')
        self.assertIsNotNone(self.repo.get_item('demo',self.item['id']))
    def test_explicit_whole_series_confirm(self):
        r=self.ai.handle_message('Удали всю серию встреч с командой')
        self.assertEqual(r['status'],'confirmation_required',r)
        self.assertIn('СЕРИЮ',r['display_text'])
        self.assertIsNotNone(self.repo.get_item('demo',self.item['id']))
        self.assertEqual(self.ai.handle_message('да')['status'],'success')
        self.assertIsNone(self.repo.get_item('demo',self.item['id']))
    def test_cannot_move_whole_without_scope(self):
        r=self.ai.handle_message('Перенеси её на завтра в 16:00')
        self.assertEqual(r['status'],'clarification')
        self.assertIsNotNone(self.repo.get_item('demo',self.item['id']))

if __name__=='__main__':unittest.main()
