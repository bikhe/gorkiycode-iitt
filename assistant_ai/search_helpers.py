"""Deterministic lexical search + small Russian synonym dictionary.
Conservative by design: no meaningless records just to fill a list.
A dedicated embedding index may be plugged into the repository later.
"""
from __future__ import annotations
import re
from .dates import norm

STOPWORDS = {
    'найди','найти','поищи','покажи','где','когда','была','было','был','есть',
    'задача','задачу','задачи','встреча','встречу','встречи',
    'напоминание','напоминания','про','для','мне','этот','эту','такой','такую',
    'я','мы','мои','мой','моя','у','с','на','по','то','что','как','которую','раньше',
    'когда-то','ставил','ставила','создай','прошу','ранее','один',
}
GROUPS = (
    ('договор','контракт','соглашен'),
    ('email','gmail','почт','емейл','имейл','мейл','письм'),
    ('позвон','телефон','звон','набрат','созвон'),
    ('слайд','презентац','выступлен','доклад'),
    ('купит','покуп','заказат','приобрет'),
    ('обсуд','совещан','собран','встреч','созвон'),
)

def _stem(word):
    if len(word) < 5: return word
    # Safe lightweight suffix removal. Not a morphology package.
    return re.sub(r'(иями|ями|ами|ого|ему|иях|иях|ов|ев|ой|ая|ое|ые|ий|ый|ей|ую|ам|ом|ях|ия|ие|ии|а|я|ы|и|у|е|ю)$','',word)


def score(query:str,title:str)->float:
    q = [_stem(w) for w in re.findall(r'[а-яa-z0-9]+',norm(query)) if w not in STOPWORDS and len(w)>=3]
    t = [_stem(w) for w in re.findall(r'[а-яa-z0-9]+',norm(title)) if len(w)>=3]
    if not q or not t:return 0.0
    matching = 0
    for term in q:
        direct = any(term == word or (len(term)>=4 and (word.startswith(term) or term.startswith(word))) for word in t)
        synonym = any(any(term.startswith(w[:4]) for w in group) and any(any(word.startswith(w[:4]) for w in group) for word in t) for group in GROUPS)
        if direct or synonym: matching+=1
    return matching / len(q)


def rank(query:str,items:list[dict],limit=30):
    rated=[(score(query,row.get('title') or ''),row) for row in items]
    rated=[(value,item) for value,item in rated if value>0]
    rated.sort(key=lambda pair:(-pair[0],str(pair[1].get('start_at') or pair[1].get('due_at') or '')))
    return [item for value,item in rated[:limit] if value>=0.4]
