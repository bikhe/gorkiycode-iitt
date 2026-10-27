"""Conservative title fallback when LLM omits a meaningful subject.
Never replace a nonempty meaningful LLM title with these heuristics.
"""
import re

MONTH_RE=r'(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяе]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)'


def fallback_title(text,kind):
    s=re.sub(r'\s+',' ',text).strip()
    # Recurrence prefixes before the verb.
    s=re.sub(r'^(?:кажд\w*\s+(?:понедельн\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресен\w*|недел\w*|день)|по\s+будням)(?:\s+в\s+\d{1,2}(?:[:.]\d{2})?)?\s+', '',s,flags=re.I)
    s=re.sub(r'^(?:напомни(?:\s+мне)?|поставь|назначь|добавь|запиши|создай|запланируй|не\s+забыть|нужно)\s+', '',s, flags=re.I)
    s=re.sub(r'^(?:задачу|встречу|напоминание|ещ[её]\s+одну\s+встречу)\s+', '',s,flags=re.I)
    # Date at beginning; examples: «в пятницу нужно отправить отчет».
    s=re.sub(r'^(?:в\s+)?(?:эту|следующую|ближайшую)?\s*(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)(?:\s+нужно)?\s+', '',s,flags=re.I)
    s=re.sub(r'^(?:сегодня|завтра|послезавтра)\s+','',s,flags=re.I)
    s=re.sub(r'^(?:в|к)\s+(?:\d{1,2}(?:[:.]\d{2})?|(?:пол|половине)\s+\S+|[а-я]+)(?:\s+(?:час(?:а|ов)?|утра|дня|вечера|ночи))?\s+','',s,flags=re.I)
    s=re.sub(r'^(?:на\s+)?(?:\d{1,2}|перв\w*|втор\w*|пят\w*)\s+(?:числ\w*\s+)?(?:моего\s+любим\w*\s+месяц\w*|'+MONTH_RE+r')\s+', '',s,flags=re.I)
    s=re.sub(r'\s+(?:сегодня|завтра|послезавтра)(?:\s+(?:с\s+\d{1,2}:\d{2}\s+до\s+\d{1,2}:\d{2}|в\s+\d{1,2}(?:[:.]\d{2})?))?\s*$', '',s,flags=re.I)
    s=re.sub(r'\s+до\s+конца\s+недел\w*\s*$', '',s,flags=re.I)
    s=re.sub(r'\s+\b(?:на|в|к)\s+\d{1,2}:\d{2}\s*$', '',s,flags=re.I)
    s=re.sub(r'^нужно\s+','',s,flags=re.I)
    s=s.strip(' .,!?')[:250]
    if not s or s.casefold() in {'задача','встреча','встречу','напоминание','не знаю','завтра'}:
        return {'meeting':'Встреча','reminder':'Напоминание','task':'Задача'}[kind]
    if kind=='meeting' and s.casefold().startswith('с '):s='Встреча '+s
    return s[0].upper()+s[1:]
