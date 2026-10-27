"""Extra spoken clock forms missed by the baseline parser.

Keep this intentionally small; the full Russian time grammar lives in dates.py.
Unknown expressions are not converted to guessed dates.
"""
import re


def additional_clock(text:str):
    s=re.sub(r'\s+',' ',text.casefold().replace('ё','е')).strip()
    # «в час дня», «в час ночи», «в час утра»
    m=re.search(r'\b(?:в|к|на)\s+час\s+(дня|ночи|утра)\b',s)
    if m:return (13 if m[1]=='дня' else 1,0)
    # «в начале третьего часа» is deliberately unsupported.
    return None


def contains_approximate_part(text:str):
    return bool(re.search(r'\b(?:утром|вечером|днем|ночью|под\s+вечер|после\s+обеда)\b',text.casefold().replace('ё','е')))


def has_ambiguous_hour(text:str):
    s=text.casefold().replace('ё','е')
    # don't treat the date «на 5 ноября» as time.
    return bool(re.search(r'\b(?:сегодня|завтра|послезавтра)\s+на\s+\d{1,2}(?!\d|\s*(?:числ|январ|феврал|март|апрел|мая|июн|июл|август|сентябр|октябр|ноябр|декабр))\b',s))
