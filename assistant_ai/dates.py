"""Deterministic date resolution; the LLM never invents a calendar timestamp."""
from __future__ import annotations
import calendar
import re
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta


def _day_hour(hour):
    """«10 часов дня» — это 10:00, «3 часа дня» — 15:00, «12 дня» — полдень."""
    if hour == 0:
        return 12  # «пол первого дня» приходит как 0 часов
    if 1 <= hour <= 8:
        return hour + 12
    return hour

class DateError(ValueError):
    pass

MONTHS = {"январ":1,"феврал":2,"март":3,"апрел":4,"май":5,"мая":5,"мае":5,"июн":6,"июл":7,"август":8,"сентябр":9,"октябр":10,"ноябр":11,"декабр":12}
DAYS = {"понедельн":0,"вторник":1,"сред":2,"четверг":3,"пятниц":4,"суббот":5,"воскресен":6}
ORDINALS = {"перв":1,"втор":2,"трет":3,"четверт":4,"пят":5,"шест":6,"седьм":7,"восьм":8,"девят":9,"десят":10,"одиннадцат":11,"двенадцат":12,"тринадцат":13,"четырнадцат":14,"пятнадцат":15,"шестнадцат":16,"семнадцат":17,"восемнадцат":18,"девятнадцат":19,"двадцат":20,"тридцат":30}
CARDINALS = {"один":1,"одну":1,"одна":1,"два":2,"две":2,"три":3,"четыре":4,"пять":5,"шесть":6,"семь":7,"восемь":8,"девять":9,"десять":10,"пятнадцать":15,"полтора":1.5,"пару":2}

def norm(s: str) -> str:
    return str(s).casefold().replace("ё", "е").strip()

def month_from_text(text: str):
    s=norm(text)
    for stem,number in MONTHS.items():
        regex=(r'\b'+stem+r'\b') if stem in ('май','мая','мае') else (r'\b'+stem+r'\w*\b')
        if re.search(regex,s):return number
    return None

def has_temporal_words(text: str) -> bool:
    s=norm(text)
    months='|'.join(k+r'\w*' for k in MONTHS)
    return bool(re.search(
        r"\b(?:сегодня|завтра|послезавтра|через|до\s+конца|недел\w*|месяц\w*|год\w*|пятниц\w*|понедельн\w*|вторник\w*|сред\w*|четверг\w*|суббот\w*|воскресен\w*|числ\w*|пол\s+[а-я]+)\b"
        +r"|\b(?:"+months+r")\b|\d{1,2}[./]\d{1,2}|\b\d{1,2}:\d{2}\b",s))

class DateResolver:
    def __init__(self,timezone_name="Europe/Moscow"):
        self.zone=ZoneInfo(timezone_name)

    def _dt(self,d:date, hm:tuple[int,int]):
        candidate=datetime.combine(d,time(*hm),self.zone)
        rt=candidate.astimezone(timezone.utc).astimezone(self.zone)
        if rt.replace(fold=0)!=candidate.replace(fold=0):
            raise DateError("Это время не существует из-за перевода часов. Уточни его.")
        if candidate.replace(fold=0).utcoffset()!=candidate.replace(fold=1).utcoffset():
            raise DateError("Уточни часовой пояс: в этот день часы переводятся.")
        return candidate.isoformat(timespec="seconds")

    _CLOCK_ORDINALS = {
        "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4,
        "пятого": 5, "шестого": 6, "седьмого": 7, "восьмого": 8,
        "девятого": 9, "десятого": 10, "одиннадцатого": 11,
        "одинадцатого": 11, "одинацатого": 11, "двенадцатого": 12,
    }
    _CLOCK_CARDINALS = {
        "один": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
        "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
        "десять": 10, "одиннадцать": 11, "одинадцать": 11,
        "одинацать": 11, "двенадцать": 12, "тринадцать": 13,
        "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
        "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
        "двадцать": 20, "двадцать один": 21,
        "двадцать два": 22, "двадцать три": 23,
    }
    _CLOCK_MINUTES = {
        "ноль": 0, "пять": 5, "десять": 10, "пятнадцать": 15,
        "двадцать": 20, "двадцать пять": 25, "тридцать": 30,
        "тридцать пять": 35, "сорок": 40, "сорок пять": 45,
        "пятьдесят": 50, "пятьдесят пять": 55,
    }

    def _colloquial_clock(self, s, short):
        """Spoken Russian time with daypart. Returns None if no match."""
        ords='|'.join(sorted(self._CLOCK_ORDINALS,key=len,reverse=True))
        cards='|'.join(sorted(self._CLOCK_CARDINALS,key=len,reverse=True))
        minutes='|'.join(sorted(self._CLOCK_MINUTES,key=len,reverse=True))
        part=r'(?:\s+(утра|дня|вечера|ночи))?'
        def search(pattern):
            if short:
                return re.fullmatch(r'\s*(?:в\s+)?'+pattern+r'\s*[.!?]?\s*',s)
            return re.search(r'\b'+pattern+r'\b',s)
        def daypart(hour,p):
            if p == 'дня':
                return _day_hour(hour)
            if p == 'вечера':
                return hour+12 if hour<12 else hour
            if p == 'ночи':
                # Conversational: «пол одиннадцатого ночи» is late
                # evening, while «пол четвертого ночи» is 03:30.
                return hour+12 if hour in (8,9,10,11) else hour%12
            if p == 'утра':
                return hour%12
            return hour
        m=search(r'(?:пол[\s-]*|половин(?:а|е|у)\s+)('+ords+r')'+part)
        if m:
            hour=(self._CLOCK_ORDINALS[m[1]]-1)%12
            return daypart(hour,m[2]),30
        m=search(r'четверть\s+('+ords+r')'+part)
        if m:
            hour=(self._CLOCK_ORDINALS[m[1]]-1)%12
            return daypart(hour,m[2]),15
        m=search(r'без\s+(четверти|пяти|десяти|пятнадцати|двадцати)\s+('+cards+r'|\d{1,2})'+part)
        if m:
            offset={'четверти':15,'пяти':5,'десяти':10,'пятнадцати':15,'двадцати':20}[m[1]]
            hour=self._CLOCK_CARDINALS.get(m[2])
            if hour is None:hour=int(m[2])
            if hour<1 or hour>23:raise DateError('Некорректное время.')
            if hour>12 and m[3] in ('утра','ночи'):
                raise DateError('Часы и часть суток противоречат друг другу.')
            # Apply daypart to the TARGET hour, then subtract one hour.
            # «без двадцати двенадцать дня» = 11:40, not 23:40.
            target_hour=daypart(hour%12,m[3]) if hour<=12 else hour
            return (target_hour-1)%24,60-offset
        if re.search(r'\bполночь\b',s):return 0,0
        if re.search(r'\bполдень\b',s):return 12,0
        # «в десять тридцать вечера», «в двадцать два часа тридцать минут»
        spoken=r'('+cards+r')(?:\s+час(?:а|ов)?)?(?:\s+('+minutes+r')(?:\s+минут\w*)?)?'+part
        if short:
            m=re.fullmatch(r'\s*(?:(?:в|на|к)\s+)?'+spoken+r'\s*[.!?]?\s*',s)
        else:
            m=re.search(r'\b(?:в|к)\s+'+spoken+r'(?=\W|$)',s)
        if m:
            hour=self._CLOCK_CARDINALS[m[1]]
            minute=self._CLOCK_MINUTES.get(m[2],0)
            p=m[3]
            if hour<=12 and p is None and m[2] is None:
                raise DateError('Уточни, это утром или вечером?')
            if hour>12 and p in ('утра','ночи'):
                raise DateError('Часы и часть суток противоречат друг другу.')
            if p == 'дня':
                hour=_day_hour(hour)
            elif p == 'вечера':
                hour=hour%12+12 if hour<=12 else hour
            elif p == 'ночи':
                hour=hour+12 if hour in (8,9,10,11) else hour%12
            elif p == 'утра':
                hour=hour%12
            return hour,minute
        return None

    def clock(self, text:str, short:bool=False):
        """Parse numeric and conversational time; NEVER treat 01.11 as 01:11."""
        from .timeparse import additional_clock
        extra=additional_clock(text)
        if extra is not None:return extra
        s=norm(text)
        s = re.sub(r'\s+', ' ', s)
        colloquial=self._colloquial_clock(s,short)
        if colloquial is not None:return colloquial

        # Explicit 11:30 / 11.30; bare «11 30» only in an expected-time reply.
        separator=r"[:.]|\s+"
        pattern=r"(\d{1,2})(?:((?:"+separator+r"))(\d{2}))?(?![./:]\d)\s*(?:час(?:а|ов)?)?\s*(утра|дня|вечера|ночи)?"
        if short:
            m=re.fullmatch(r"\s*(?:(?:в|на|к)\s+)?"+pattern+r"\s*[.!?]?\s*",s)
        else:
            m=None
            for candidate in re.finditer(r"(?<!\w)(?:в|к|на)\s+"+pattern+r"(?=\W|$)",s):
                # "в 5 сентября" is a date, not a clock
                tail=s[candidate.end():].strip()
                if re.match(r"(?:числ\w*|январ\w*|феврал\w*|март\w*|апрел\w*|мая|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\b",tail):
                    continue
                # Dotted DD.MM with a valid month is a DATE after «на».
                # But «в 11.30», «на 11.30» are explicit clock times.
                full=candidate.group().strip()
                if full.startswith('на ') and candidate[2]=='.' and candidate[3] is not None and int(candidate[3])<=12:
                    continue
                if full.startswith('на ') and candidate[3] is None and candidate[4] is None:
                    continue
                m=candidate;break
        if not m:return None
        h,minute,part=int(m[1]),int(m[3] or 0),m[4]
        if h>23 or minute>59:raise DateError("Некорректное время: используй часы 0–23 и минуты 0–59.")
        if part:
            if h>12 and part in ("утра","ночи"):raise DateError("Время и часть суток противоречат друг другу.")
            if part=="дня":h=_day_hour(h)
            elif part=="вечера":h=h%12+12 if h<=12 else h
            else:
                if part=="ночи":
                    h=h+12 if h in (8,9,10,11) else h%12
                else:
                    h=h%12
        elif h<=12 and m[3] is None:
            raise DateError(f"Уточни, {h:02d}:00 или {(h+12)%24:02d}:00? Можно указать время в формате ЧЧ:ММ.")
        return h,minute

    def point(self,text:str,now:datetime,favorite_month=None,base=None):
        s=norm(text)
        today=now.astimezone(self.zone).date()
        if re.search(r"\b(?:квартил\w*|четверт\w*\s+месяц\w*|полмесяца)\b",s):
            raise DateError("Срок неоднозначен. Назови точную дату или количество дней.")
        s=re.sub(r"\bчерез\s+(год|месяц|неделю|неделя|день|сутки|час)\b",lambda m:"через один "+m[1],s)
        target=None
        # Common voice shorthand: «через полчаса», «через 2 часа 30 минут».
        if re.search(r"\bчерез\s+полчаса\b",s):
            return (now.astimezone(self.zone)+timedelta(minutes=30)).isoformat(timespec='seconds')
        half_hour = re.search(r'\bчерез\s+(\d+|два|две|три|четыре|пять|один|час|полтора)\s+(?:с\s+половиной\s+)?час\w*\b',s)
        if re.search(r'\bчерез\s+(?:полтора\s+часа|\d+\s+с\s+половиной\s+часа|два\s+с\s+половиной\s+часа)\b',s):
            mhalf=re.search(r'\bчерез\s+(полтора|\d+|два|две)\s+(?:с\s+половиной\s+)?час\w*',s)
            n=(1.5 if mhalf[1]=='полтора' else (float(mhalf[1]) if mhalf[1].isdigit() else 2.0)+0.5)
            return (now.astimezone(self.zone)+timedelta(hours=n)).isoformat(timespec='seconds')
        short_compound=re.search(r'\bчерез\s+(\d+|один|одна|два|три|четыре|пять)?\s*час\w*\s+(\d{1,2}|пять|десять|пятнадцать|двадцать|тридцать|сорок|пятьдесят)(?!\d|\s*минут)\b',s)
        if short_compound:
            hours=int(short_compound[1]) if short_compound[1] and short_compound[1].isdigit() else {'один':1,'одна':1,'два':2,'три':3,'четыре':4,'пять':5,None:1}.get(short_compound[1],1)
            minutes=int(short_compound[2]) if short_compound[2].isdigit() else {'пять':5,'десять':10,'пятнадцать':15,'двадцать':20,'тридцать':30,'сорок':40,'пятьдесят':50}[short_compound[2]]
            if minutes>59:raise DateError('Уточни количество минут.')
            return (now.astimezone(self.zone)+timedelta(hours=hours,minutes=minutes)).isoformat(timespec='seconds')
        compound=re.search(r"\bчерез\s+(\d+)\s+час\w*\s+(\d+)\s+минут\w*\b",s)
        if compound:
            hours,minutes=int(compound[1]),int(compound[2])
            if hours>10000 or minutes>59:raise DateError('Уточни количество часов и минут.')
            return (now.astimezone(self.zone)+timedelta(hours=hours,minutes=minutes)).isoformat(timespec='seconds')
        m=re.search(r"\bчерез\s+(\d+|[а-я]+)\s+(минут\w*|час\w*|дн\w*|день|сут\w*|недел\w*|месяц\w*|год\w*|лет)\b",s)
        if m:
            n=float(m[1]) if m[1].isdigit() else CARDINALS.get(m[1]);unit=m[2]
            if n is None or n<=0 or n>10000:raise DateError("Сколько именно времени должно пройти?")
            if unit.startswith(("минут","час")):
                delta=timedelta(minutes=n if unit.startswith("минут") else 60*n)
                return (now.astimezone(self.zone)+delta).isoformat(timespec="seconds")
            if n!=int(n):raise DateError("Уточни число дней, недель или месяцев.")
            if unit.startswith(("дн","день","сут")):target=today+timedelta(days=int(n))
            elif unit.startswith("недел"):target=today+timedelta(weeks=int(n))
            elif unit.startswith("месяц"):target=today+relativedelta(months=int(n))
            else:target=today+relativedelta(years=int(n))
        if target is None:
            m=re.search(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?(?!\d)",s)
            if m:
                # 11.30 in «2 ноября в 11.30» is a CLOCK, not Nov 30.
                looks_like_clock=(bool(re.search(r"\b(?:в|к)\s+"+re.escape(m[0])+r"\b",s)) or (int(m[2])>12 and bool(re.search(r"\bна\s+"+re.escape(m[0])+r"\b",s))))
                if not looks_like_clock:
                    year=int(m[3]) if m[3] else today.year
                    try:
                        target=date(year,int(m[2]),int(m[1]))
                        if not m[3] and target<today:target=date(year+1,int(m[2]),int(m[1]))
                    except ValueError:raise DateError("Такой календарной даты нет.")
        if target is None:
            month=month_from_text(s)
            if "любим" in s and "месяц" in s:
                if favorite_month is None:raise DateError("А какой твой любимый месяц?")
                month=favorite_month
            explicit_next=bool(re.search(r"\bследующ\w*\s+месяц\w*\b",s))
            if explicit_next:month=today.month%12+1
            if month is not None:
                # Restrict day to the part directly before the month or "число", not a time.
                dm=re.search(r"\b(\d{1,2})\s*(?:-?(?:е|го))?\s*(?:числ\w*\s+)?(?:[а-я]+)?",s)
                day=int(dm[1]) if dm and 1<=int(dm[1])<=31 else None
                if day is None:
                    day=next((v for stem,v in ORDINALS.items() if re.search(r"\b"+stem+r"\w*\b",s)),None)
                if day is not None:
                    ym=re.search(r"\b(20\d{2}|21\d{2})\b",s)
                    year=int(ym[1]) if ym else today.year+(today.month==12 and explicit_next)
                    try:
                        target=date(year,month,day)
                        if not ym and not explicit_next and target<today:target=date(year+1,month,day)
                    except ValueError:raise DateError("Такой календарной даты нет.")
        if target is None:
            if re.search(r"\bпослезавтра\b",s):target=today+timedelta(days=2)
            elif re.search(r"\bзавтра\b",s):target=today+timedelta(days=1)
            elif re.search(r"\bсегодня\b",s):target=today
            elif re.search(r"\bконц\w*\s+(?:этой\s+)?недел\w*",s):target=today+timedelta(days=6-today.weekday())
            elif re.search(r"\bконц\w*\s+(?:этого\s+)?месяц\w*",s):target=today.replace(day=calendar.monthrange(today.year,today.month)[1])
            else:
                found=[d for stem,d in DAYS.items() if re.search(r"\b"+stem+r"\w*\b",s)]
                if len(found)==1:
                    d=found[0]
                    if re.search(r"\bследующ\w*",s):target=today-timedelta(days=today.weekday())+timedelta(days=7+d)
                    else:target=today+timedelta(days=(d-today.weekday())%7)
        if target is None and base:target=date.fromisoformat(base[:10])
        if target is None:return None
        from .timeparse import contains_approximate_part,has_ambiguous_hour
        clock=self.clock(s)
        if clock is None and contains_approximate_part(s):
            raise DateError('На какое точное время? Например, 19:00.')
        if clock is None and has_ambiguous_hour(s):
            raise DateError('Ты имеешь в виду время или число месяца? Уточни, например, 17:00.')
        return self._dt(target,clock) if clock else target.isoformat()

    def interval(self,text,now):
        s=norm(text);today=now.astimezone(self.zone).date();monday=today-timedelta(days=today.weekday())
        if re.search(r"\bследующ\w*\s+недел\w*",s):return monday+timedelta(days=7),monday+timedelta(days=13)
        if re.search(r"\b(?:этой|эту|текущей)\s+недел\w*",s):return monday,monday+timedelta(days=6)
        if re.search(r"\bвыходн\w*",s):return monday+timedelta(days=5),monday+timedelta(days=6)
        m=re.search(r"\b(?:ближайш\w*|следующ\w*)\s+(\d+|[а-я]+)\s+дн\w*",s)
        if m:
            n=float(m[1]) if m[1].isdigit() else CARDINALS.get(m[1])
            if n and int(n)==n and 1<=n<=366:return today,today+timedelta(days=int(n)-1)
        for phrase,offset in (("послезавтра",2),("завтра",1),("сегодня",0)):
            if re.search(r"\b"+phrase+r"\b",s):d=today+timedelta(days=offset);return d,d
        if re.search(r"\b(?:на|за)\s+(?:этот|текущ\w*)\s+месяц",s):
            return today.replace(day=1),today.replace(day=calendar.monthrange(today.year,today.month)[1])
        
        month=month_from_text(s)
        if month:
            # Если указано конкретное число месяца,
            # возвращаем один день, а не весь месяц.
            month_stems='|'.join(re.escape(x)+r'\w*' for x in MONTHS)
            ordinal_stems='|'.join(re.escape(x)+r'\w*' for x in ORDINALS)

            has_day=bool(re.search(
                r'\b(?:[0-3]?\d(?:-?го|-?е)?|(?:'+ordinal_stems+r'))\s+'
                r'(?:числ\w*\s+)?(?:'+month_stems+r')\b',s
            ))

            if has_day:
                at=self.point(s,now)
                if at:
                    day=date.fromisoformat(at[:10])
                    return day,day

            year=today.year if month>=today.month else today.year+1

            explicit=re.search(r"\b(20\d{2}|21\d{2})\b",s)
            if explicit:year=int(explicit[1])
            return date(year,month,1),date(year,month,calendar.monthrange(year,month)[1])
        at=self.point(s,now)
        if at:
            d=date.fromisoformat(at[:10]);return d,d
        return None
