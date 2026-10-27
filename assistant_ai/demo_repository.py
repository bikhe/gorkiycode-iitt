"""Local in-memory implementation of the Django CRUD contract for tests/demo.
Never use as production storage.
"""
from __future__ import annotations
import copy
import re
from datetime import date
from .dates import norm

class DemoRepository:
    def __init__(self):
        self.items=[];self.sessions={};self.request_ids={};self.next_id=1
    def _scoped(self,user):return [x for x in self.items if x['user_id']==user and not x.get('deleted')]
    def get_session(self,user_id,session_id):return copy.deepcopy(self.sessions.get((user_id,session_id),{}))
    def save_session(self,user_id,session_id,state):self.sessions[(user_id,session_id)]=copy.deepcopy(state)
    def create_item(self,user_id,data,request_id):
        key=(user_id,request_id)
        if key in self.request_ids:return self.get_item(user_id,self.request_ids[key])
        record=copy.deepcopy(data);record.update(id=self.next_id,user_id=user_id,created_at=date.today().isoformat())
        self.items.append(record);self.request_ids[key]=self.next_id;self.next_id+=1
        return copy.deepcopy(record)
    def get_item(self,user_id,item_id):
        return next((copy.deepcopy(x) for x in self._scoped(user_id) if x['id']==item_id),None)
    def list_items(self,user_id,date_from,date_to,kind=None):
        # Include repeating templates here: service expands them in conflict checks.
        return [copy.deepcopy(x) for x in self._scoped(user_id)
                if (kind is None or kind==x['type'])
                and (not (x.get('start_at') or x.get('due_at')) or x.get('recurrence') or
                     date_from <= (x.get('start_at') or x.get('due_at'))[:10] <= date_to)]
    def search_items(self,user_id,query):
        from .search_helpers import rank
        return rank(query,[copy.deepcopy(x) for x in self._scoped(user_id)])
    def update_item(self,user_id,item_id,changes):
        for item in self.items:
            if item['user_id']==user_id and item['id']==item_id and not item.get('deleted'):
                item.update(copy.deepcopy(changes));return copy.deepcopy(item)
        raise ValueError('Запись не найдена.')
    def delete_item(self,user_id,item_id):
        for item in self.items:
            if item['user_id']==user_id and item['id']==item_id and not item.get('deleted'):
                item['deleted']=True;return True
        return False
