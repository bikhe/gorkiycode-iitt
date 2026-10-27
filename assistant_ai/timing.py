"""Independent interpretation of meeting duration and explicit numeric ranges."""
import re
from .dates import norm


def numeric_range(text):
    s=norm(text)
    m=re.search(r'\bс\s+(\d{1,2}):(\d{2})\s+до\s+(\d{1,2}):(\d{2})\b',s)
    if not m:return None
    h1,mi1,h2,mi2=map(int,m.groups())
    if h1>23 or h2>23 or mi1>59 or mi2>59:raise ValueError('Уточни время начала и окончания встречи.')
    begin=h1*60+mi1;end=h2*60+mi2
    if end<=begin:raise ValueError('Время окончания должно быть позже времени начала в тот же день.')
    if end-begin>720:raise ValueError('Слишком длинная встреча: уточни продолжительность.')
    new=s[:m.start()]+f'в {h1:02d}:{mi1:02d}'+s[m.end():]
    return new,end-begin


def duration_minutes(text,from_model):
    s=norm(text)
    m=re.search(r'\b(?:на|продолжительностью|длительностью)\s+(полтора|\d+|один|два|две|три|четыре)\s+(час\w*|минут\w*)\b',s)
    if m:
        n={'полтора':1.5,'один':1,'два':2,'две':2,'три':3,'четыре':4}.get(m[1],float(m[1]) if m[1].isdigit() else None)
        if n is None:raise ValueError('Уточни длительность встречи.')
        result=round(n*(60 if m[2].startswith('час') else 1))
    elif from_model is not None and str(from_model).strip().casefold() not in ('','null','none'):
        try:result=int(from_model)
        except (TypeError,ValueError,OverflowError):raise ValueError('Неверная длительность встречи.')
    else:result=60
    if not 1<=result<=720:raise ValueError('Длительность встречи должна быть от 1 до 720 минут.')
    return result
