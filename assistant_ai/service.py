
"""Safe orchestration layer. No HTTP, SQLite, Django or STT dependency."""
from __future__ import annotations

import re
import calendar
import uuid
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from dateutil.rrule import (
    rrule, DAILY, WEEKLY, MONTHLY, YEARLY,
    MO, TU, WE, TH, FR, SA, SU
)

from .dates import (
    DateResolver, DateError, norm, month_from_text,
    DAYS, ORDINALS, has_temporal_words
)
from .timing import numeric_range, duration_minutes


class UserInputError(ValueError):
    pass


class ClarifyKind(Exception):
    """Запрос понятен, кроме типа записи; ответ пользователя его достроит."""


class AmbiguousTarget(UserInputError):
    def __init__(self, rows):
        self.rows = rows[:5]
        super().__init__('Подходят несколько записей.')


def _records(n):
    """1 запись, 2 записи, 5 записей."""
    if n % 10 == 1 and n % 100 != 11:
        return f'{n} запись'
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f'{n} записи'
    return f'{n} записей'


def _human(value, zone):
    if not value:
        return "без срока"

    if len(value) == 10:
        return date.fromisoformat(value).strftime(
            "%d.%m.%Y (время не указано)"
        )

    return datetime.fromisoformat(value).astimezone(zone).strftime(
        "%d.%m.%Y в %H:%M"
    )


def _response(status, message, action=None, item=None, **extras):
    data = {
        'status': status,
        'display_text': message,
        'speech_text': message,
        'action': action,
        'item': item
    }
    data.update(extras)
    return data


def _kind_from_text(text):
    s = norm(text)

    if re.search(r"\b(?:встреч\w*|созвон\w*)\b", s):
        return 'meeting'

    if re.search(r"\b(?:напомни|напоминани\w*)\b", s):
        return 'reminder'

    if re.search(r"\bзадач\w*\b", s):
        return 'task'

    return None


_MONTHS = (
    r'январ\w*|феврал\w*|март\w*|апрел\w*|мая|мае|июн\w*|июл\w*|'
    r'август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*'
)
_WEEKDAYS = (
    r'понедельн\w*|вторник\w*|сред[ауы]|четверг\w*|'
    r'пятниц\w*|суббот\w*|воскресен\w*'
)


def _strip_temporal(text):
    """Убирает из названия слова о времени: «встреча с Иваном завтра
    в 10 часов дня» → «встреча с иваном». Дату и час проверяют отдельно."""
    s = norm(text or '')
    patterns = (
        r'\b(?:котор\w+|что|чтобы)\s+(?:должн\w+\s+)?(?:быть|будет|был\w*|стоит)\b',
        r'\b(?:на|в|во|к|до)?\s*(?:сегодня|завтра|послезавтра)\b',
        # «в среду», но не «задачу про среду обитания».
        r'\b(?:на|в|во)\s+(?:(?:эт\w+|следующ\w+)\s+)?(?:' + _WEEKDAYS + r')\b',
        r'\b(?:на|в)?\s*(?:эт\w+|следующ\w+)\s+недел\w*',
        r'\b(?:на|в|к)?\s*\d{1,2}\s+(?:' + _MONTHS + r')\b',
        r'\b(?:на|в|к)?\s*\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b',
        r'\b(?:в|на|к)\s+\d{1,2}(?:[:.]\d{2})?(?:\s+час\w*)?'
        r'(?:\s+(?:утра|дня|вечера|ночи))?\b',
        r'\b(?:в|на|к)\s+(?:один|два|три|четыре|пять|шесть|семь|восемь|'
        r'девять|десять|одиннадцать|двенадцать)\s+час\w*'
        r'(?:\s+(?:утра|дня|вечера|ночи))?\b',
        r'\b(?:утром|вечером|днем|днём|ночью)\b',
    )
    for pattern in patterns:
        s = re.sub(pattern, ' ', s)
    # Предлог, от которого отрезали дату: «перенеси на (послезавтра)».
    s = re.sub(r'(?:^|\s)(?:на|в|во|к|до)\s*$', ' ', s)
    return re.sub(r'\s+', ' ', s).strip(' ,.;:-')


def _fallback_title(text, kind):
    from .title_helpers import fallback_title
    return fallback_title(text, kind)


def _recurrence_from_text(text):
    """Deterministic recurring expressions. Unsupported rules require clarification."""
    s = norm(text)

    weekdays = [
        day
        for stem, day in DAYS.items()
        if re.search(r'\b' + stem + r'\w*\b', s)
    ]

    # «по понедельникам», «каждый понедельник и четверг», «по будням».
    weekly = bool(
        re.search(
            r'\bкажд\w*\s+(?:понедельн|вторник|сред|четверг|пятниц|суббот|воскресен)',
            s
        )
        or re.search(
            r'\bпо\s+(?:понедельн|вторник|сред|четверг|пятниц|суббот|воскресен)',
            s
        )
    )

    each_2 = bool(
        re.search(
            r'\b(?:раз\s+в\s+две|кажд\w*\s+две|кажд\w*\s+втор\w*)\s+недел\w*',
            s
        )
    )

    if weekly or each_2:
        # 'первый понедельник месяца' cannot be represented by weekly RRULE.
        if re.search(
            r'\b(?:перв\w*|втор\w*|последн\w*)\s+'
            r'(?:понедельн|вторник|сред|четверг|пятниц|суббот|воскресен)\w*'
            r'.*\bмесяц\w*',
            s
        ):
            raise UserInputError(
                'Пока не поддерживается «первый/последний понедельник месяца». '
                'Уточни правило.'
            )

        return {
            'freq': 'weekly',
            'interval': 2 if each_2 else 1,
            'by_weekday': sorted(set(weekdays))
        }

    if re.search(r'\b(?:ежедневно|кажд\w*\s+день)\b', s):
        return {'freq': 'daily', 'interval': 1}

    if re.search(r'\bпо\s+будням\b', s):
        return {
            'freq': 'weekly',
            'interval': 1,
            'by_weekday': list(range(5))
        }

    if re.search(r'\b(?:кажд\w*\s+недел\w*|еженедельно)\b', s):
        return {
            'freq': 'weekly',
            'interval': 1,
            'by_weekday': sorted(set(weekdays))
        }

    if re.search(
        r'\b(?:ежемесячно|кажд\w*\s+месяц\w*|'
        r'кажд\w*\s+\w+\s+числ\w*|'
        r'числ\w*\s+кажд\w*\s+месяц\w*)\b',
        s
    ):
        value = None

        for stem, d in ORDINALS.items():
            if re.search(r'\b' + stem + r'\w*\s+числ\w*', s):
                value = d
                break

        if value is None:
            m = re.search(
                r'\b(\d{1,2})(?:-?(?:го|е))?\s+числ\w*',
                s
            )
            if m:
                value = int(m[1])

        return {
            'freq': 'monthly',
            'interval': 1,
            'bymonthday': value
        }

    if re.search(r'\b(?:ежегодно|кажд\w*\s+год)\b', s):
        return {'freq': 'yearly', 'interval': 1}

    return None


def _validate_recurrence(data):
    """Accept optional fields omitted, None, or JSON-string 'null' from LLM."""

    def empty(v):
        return v is None or (
            isinstance(v, str)
            and v.strip().casefold() in (
                '', 'null', 'none', 'undefined'
            )
        )

    if empty(data):
        return None

    if not isinstance(data, dict):
        raise UserInputError('Уточни правило повторения.')

    freq = data.get('freq')

    if freq not in ('daily', 'weekly', 'monthly', 'yearly'):
        raise UserInputError(
            'Не удалось определить частоту повторения.'
        )

    try:
        interval = (
            int(data['interval'])
            if not empty(data.get('interval'))
            else 1
        )

        rawdays = data.get('by_weekday')
        weekdays = (
            [int(i) for i in rawdays]
            if isinstance(rawdays, list)
            else []
        )

        bymonthday = (
            int(data['bymonthday'])
            if not empty(data.get('bymonthday'))
            else None
        )

        count = (
            int(data['count'])
            if not empty(data.get('count'))
            else None
        )

        until = (
            str(data['until'])
            if not empty(data.get('until'))
            else None
        )

    except (ValueError, TypeError, OverflowError):
        raise UserInputError(
            'Некорректные параметры повторения.'
        )

    if (
        not 1 <= interval <= 120
        or any(x not in range(7) for x in weekdays)
    ):
        raise UserInputError(
            'Некорректная периодичность.'
        )

    if bymonthday is not None and not 1 <= bymonthday <= 31:
        raise UserInputError(
            'Некорректное число месяца.'
        )

    if count is not None and not 1 <= count <= 5000:
        raise UserInputError(
            'Некорректное число повторений.'
        )

    if until:
        try:
            date.fromisoformat(until)
        except ValueError:
            raise UserInputError(
                'Некорректная дата окончания повторения.'
            )

    return {
        'freq': freq,
        'interval': interval,
        'by_weekday': weekdays,
        'bymonthday': bymonthday,
        'count': count,
        'until': until
    }


def _occurrences(item, start, end, zone, max_n=500):
    at = item.get('start_at') or item.get('due_at')

    if not at:
        return []

    anchor = (
        datetime.fromisoformat(at)
        if 'T' in at
        else datetime.combine(
            date.fromisoformat(at),
            time.min,
            zone
        )
    )

    rule = item.get('recurrence')

    if not rule:
        return [anchor] if start <= anchor <= end else []

    freq = {
        'daily': DAILY,
        'weekly': WEEKLY,
        'monthly': MONTHLY,
        'yearly': YEARLY
    }[rule['freq']]

    params = {
        'dtstart': anchor,
        'interval': rule.get('interval', 1)
    }

    if rule.get('by_weekday'):
        params['byweekday'] = [
            [MO, TU, WE, TH, FR, SA, SU][x]
            for x in rule['by_weekday']
        ]

    if rule.get('bymonthday'):
        params['bymonthday'] = rule['bymonthday']

    if rule.get('until'):
        params['until'] = datetime.combine(
            date.fromisoformat(rule['until']),
            time.max,
            zone
        )

    if rule.get('count'):
        params['count'] = rule['count']

    return list(
        rrule(freq, **params).between(start, end, inc=True)
    )[:max_n]


class AssistantAI:
    def __init__(
        self,
        planner,
        crud,
        timezone_name='Europe/Moscow',
        now_func=None
    ):
        self.planner, self.crud = planner, crud
        self.cal = DateResolver(timezone_name)
        self.zone = self.cal.zone
        self.now_func = now_func or (
            lambda: datetime.now(self.zone)
        )

    @staticmethod
    def _hour_hint(text):
        """Находит названный час, не угадывая утро или вечер."""
        words = {
            'час': 1,
            'один': 1,
            'два': 2,
            'три': 3,
            'четыре': 4,
            'пять': 5,
            'шесть': 6,
            'семь': 7,
            'восемь': 8,
            'девять': 9,
            'десять': 10,
            'одиннадцать': 11,
            'двенадцать': 12
        }

        s = norm(text)
        names = '|'.join(words)

        pattern = (
            r'\b(?:в|на|к)\s+(\d{1,2}|'
            + names +
            r')(?:\s+час\w*)?\b'
        )

        for match in re.finditer(pattern, s):
            tail = s[match.end():]

            # «На 5 ноября» — дата, а не время.
            if re.match(
                r'\s*(?:январ|феврал|март|апрел|мая|'
                r'июн|июл|август|сентябр|октябр|'
                r'ноябр|декабр)\w*\b',
                tail
            ):
                continue

            value = match[1]

            hour = (
                int(value)
                if value.isdigit()
                else words[value]
            )

            if 1 <= hour <= 12:
                return hour

        return None

    @staticmethod
    def _daypart_hint(text):
        """Запоминает уточнение: утром, днём, вечером, ночью."""
        s = norm(text)

        for stem, form in (
            ('вечер', 'вечера'),
            ('утр', 'утра'),
            ('дн', 'дня'),
            ('ноч', 'ночи')
        ):
            if re.search(r'\b' + stem + r'\w*\b', s):
                return form

        return None

    def _recover_day(self, text, now):
        """Восстанавливает дату, если час оказался неоднозначным."""
        s = norm(text)

        pattern = (
            r'\b(?:в|на|к)\s+'
            r'(?:\d{1,2}|час|один|два|три|четыре|пять|'
            r'шесть|семь|восемь|девять|десять|'
            r'одиннадцать|двенадцать)'
            r'(?:\s+час\w*)?\b'
        )

        for match in re.finditer(pattern, s):
            tail = s[match.end():]

            if re.match(
                r'\s*(?:январ|феврал|март|апрел|мая|'
                r'июн|июл|август|сентябр|октябр|'
                r'ноябр|декабр)\w*\b',
                tail
            ):
                continue

            cleaned = (
                s[:match.start()] + ' ' + s[match.end():]
            ).strip()

            try:
                result = self.cal.point(cleaned, now)
            except DateError:
                result = None

            if result:
                return result[:10]

        return None

    def _reply(self, user, sid, state, text, result):
        history = state.get('history') or []

        state['history'] = (
            history + [{
                'user': text[:400],
                'assistant': result['display_text'][:400]
            }]
        )[-8:]

        # Запоминаем результат последнего действия.
        # Это позволит сказать "Нет, лучше завтра в 18:00".
        item = result.get('item')

        state['last_result'] = {
            'status': result.get('status'),
            'action': result.get('action'),
            'item_id': (
                item.get('id')
                if isinstance(item, dict)
                else None
            )
        }

        self.crud.save_session(user, sid, state)
        return result

    def _implicit_time_correction(
        self, user, state, text, now
    ):
        """Исправление даты или времени последней записи."""
        previous = state.get('last_result') or {}

        # Исправляем только после успешного создания
        # или изменения записи.
        if (
            previous.get('status') != 'success'
            or previous.get('action') not in (
                'create', 'update'
            )
        ):
            return None

        s = norm(text)

        # Определяем естественную фразу исправления.
        if not re.match(
            r'^(?:(?:нет|не),?[!?.]?\s+)?'
            r'(?:лучше|давай|пусть\s+будет)\b',
            s
        ):
            return None

        # В запросе должно присутствовать новое время или дата.
        if (
            not has_temporal_words(s)
            and self.cal.clock(s) is None
        ):
            return None

        item_id = previous.get('item_id')

        row = (
            self.crud.get_item(user, item_id)
            if item_id is not None
            else None
        )

        if not row:
            return None

        # Передаём запрос в существующую
        # безопасную логику обновления.
        plan = {
            'action': 'update',
            'reference': 'её',
            'temporal_text': text,
            '_reschedule': True
        }

        return self._apply_update(
            user, state, row, plan, text, now
        )

    def _next_repeat(
        self, rule, text, now, base=None
    ):
        clock = self.cal.clock(text)

        if (
            clock is None
            and base
            and len(base) > 10
        ):
            dt = datetime.fromisoformat(base).astimezone(
                self.zone
            )
            clock = (dt.hour, dt.minute)

        # Without an explicit time, tasks and reminders may recur by DATE.
        hm = clock or (0, 0)
        anchor_day = now.date()

        if (
            rule['freq'] == 'weekly'
            and rule.get('by_weekday')
        ):
            anchor_day = min(
                now.date() + timedelta(
                    days=(d - now.weekday()) % 7
                )
                for d in rule['by_weekday']
            )

            if datetime.combine(
                anchor_day, time(*hm), self.zone
            ) <= now:
                anchor_day += timedelta(days=7)

        elif (
            rule['freq'] == 'monthly'
            and rule.get('bymonthday')
        ):
            target = rule['bymonthday']

            for i in range(24):
                month = now.date() + relativedelta(
                    months=i, day=1
                )

                if target <= calendar.monthrange(
                    month.year, month.month
                )[1]:
                    d = month.replace(day=target)

                    if datetime.combine(
                        d, time(*hm), self.zone
                    ) > now:
                        anchor_day = d
                        break
            else:
                raise UserInputError(
                    'Уточни дату начала повторения.'
                )

        anchor = datetime.combine(
            anchor_day, time(*hm), self.zone
        )

        item = {
            'start_at': anchor.isoformat(),
            'recurrence': rule
        }

        nexts = _occurrences(
            item,
            now + timedelta(seconds=1),
            now + timedelta(days=370),
            self.zone
        )

        if not nexts:
            raise UserInputError(
                'Не удалось найти ближайшее повторение. '
                'Уточни дату начала.'
            )

        return (
            nexts[0].isoformat(timespec='seconds')
            if clock
            else nexts[0].date().isoformat()
        )

    def _conflicts(
        self, user, proposal, ignore=None
    ):
        if (
            proposal['type'] != 'meeting'
            or not proposal.get('start_at')
        ):
            return []

        at = datetime.fromisoformat(proposal['start_at'])
        end = at + timedelta(days=370)

        candidates = self.crud.list_items(
            user,
            (at - timedelta(days=1)).date().isoformat(),
            end.date().isoformat(),
            'meeting'
        )

        new = _occurrences(
            proposal, at, end, self.zone
        )

        hits = []

        for other in candidates:
            if (
                other['id'] == ignore
                or not other.get('start_at')
            ):
                continue

            old = _occurrences(
                other,
                at - timedelta(days=1),
                end,
                self.zone
            )

            for a in new:
                if any(
                    a < b + timedelta(
                        minutes=other.get('duration_minutes') or 60
                    )
                    and b < a + timedelta(
                        minutes=proposal.get('duration_minutes') or 60
                    )
                    for b in old
                ):
                    hits.append(other)
                    break

            if len(hits) >= 5:
                break

        return hits

    def _commit_create(
        self, user, state, p, now, confirmed=False
    ):
        kind = p['type']
        at = p.get('at')
        rule = p.get('recurrence')

        # «Каждую неделю в 20:00» does not contain a weekday:
        # ask rather than silently invent a Monday.
        if (
            rule
            and rule['freq'] == 'weekly'
            and not rule.get('by_weekday')
        ):
            p['need'] = 'weekday'
            state['pending'] = p

            return _response(
                'clarification',
                'В какой день недели повторять встречу? '
                'Например, каждый четверг.',
                'create'
            )

        if (
            rule
            and (
                not at
                or (
                    'T' in at
                    and datetime.fromisoformat(at) <= now
                )
            )
        ):
            at = self._next_repeat(
                rule,
                p.get('temporal_text', ''),
                now,
                p.get('at')
            )
            p['at'] = at

        if kind in ('meeting', 'reminder') and not at:
            p['need'] = 'date'
            state['pending'] = p

            return _response(
                'clarification',
                'На какую дату поставить запись?',
                'create',
                suggestions=['Сегодня', 'Завтра', 'Послезавтра']
            )

        if kind == 'meeting' and 'T' not in at:
            p['need'] = 'time'
            state['pending'] = p

            return _response(
                'clarification',
                'На какое время назначить встречу?',
                'create',
                suggestions=['В 10:00', 'В 15:00', 'В 18:00']
            )

        if not at and p.get('date_required'):
            p['need'] = 'date'
            state['pending'] = p

            return _response(
                'clarification',
                'Уточни дату: не хочу сохранять задачу '
                'без указанного срока.',
                'create'
            )

        if at:
            parsed = (
                datetime.fromisoformat(at)
                if 'T' in at
                else date.fromisoformat(at)
            )

            if (
                isinstance(parsed, datetime)
                and parsed <= now
            ):
                raise UserInputError(
                    'Указанное время уже прошло.'
                )

            if (
                isinstance(parsed, date)
                and not isinstance(parsed, datetime)
                and parsed < now.date()
            ):
                raise UserInputError(
                    'Указанная дата уже прошла.'
                )

        data = {
            'type': kind,
            'title': p['title'],
            'start_at': at if kind == 'meeting' else None,
            'due_at': at if kind != 'meeting' else None,
            'duration_minutes': p.get('duration_minutes') or 60,
            'recurrence': rule,
            'status': 'pending'
        }

        if not confirmed:
            candidates = self.crud.search_items(
                user, p['title']
            )

            duplicates = [
                x
                for x in candidates
                if (
                    x.get('type') == kind
                    and norm(x.get('title', '')) == norm(p['title'])
                    and (
                        x.get('start_at') or x.get('due_at')
                    ) == at
                    and x.get('recurrence') == rule
                )
            ]

            if duplicates:
                p['need'] = 'duplicate'
                state['pending'] = p

                return _response(
                    'confirmation_required',
                    'Такая запись уже есть. Создать ещё одну? '
                    'Ответь «да» или «нет».',
                    'create',
                    existing_id=duplicates[0]['id']
                )

            overlaps = self._conflicts(user, data)

            if overlaps:
                p['need'] = 'conflict'
                state['pending'] = p

                names = '; '.join(
                    f"«{x['title']}» "
                    f"({_human(x['start_at'], self.zone)})"
                    for x in overlaps
                )

                return _response(
                    'confirmation_required',
                    f'Встреча пересекается с {names}. '
                    'Всё равно создать?',
                    'create',
                    conflicts=[x['id'] for x in overlaps]
                )

        item = self.crud.create_item(
            user,
            data,
            p['request_id']
        )

        state['pending'] = None
        state['last_item_id'] = item['id']

        frequency = (
            (
                ' каждый день'
                if rule['freq'] == 'daily'
                else ' каждую неделю'
                if rule['freq'] == 'weekly'
                else ' каждый месяц'
                if rule['freq'] == 'monthly'
                else ' каждый год'
            )
            if rule else ''
        )

        label = {
            'task': 'Задача',
            'meeting': 'Встреча',
            'reminder': 'Напоминание'
        }[kind]

        word = (
            'создано'
            if kind == 'reminder'
            else 'создана'
        )

        screen = (
            f"{label} {word}: «{item['title']}» — "
            f"{_human(at, self.zone)}{frequency}."
        )

        speech = (
            f"Готово! {label.lower()} "
            f"«{item['title']}» {word}."
            + (
                f" Дата: {_human(at, self.zone)}."
                if at
                else ''
            )
        )

        return {
            'status': 'success',
            'action': 'create',
            'item': item,
            'display_text': screen,
            'speech_text': speech
        }

    @staticmethod
    def _reference_from_command(text):
        """Извлекает название записи из команды, не полагаясь на LLM.

        В «Перенеси встречу с Анной на 29 октября в 17:00» новая дата
        не должна попадать в строку поиска исходной встречи.
        """
        s = norm(text)
        # «А перенеси её…», «Ну удали…», «Пожалуйста, перенеси…»
        command = re.match(
            r'^(?:(?:а|и|ну|так|теперь|тогда|давай|пожалуйста)'
            r'[\s,]+)*'
            r'(?:удали|удалить|убери|сотри|стереть|'
            r'перенеси|перенести|передвинь|передвинуть|'
            r'сдвинь|сдвинуть|перепланируй)\b\s*(.*)',
            s
        )
        if not command:
            return ''

        reference = command.group(1).strip()
        # «Удали всю серию встреч с командой» — ищем именно встречу.
        reference = re.sub(
            r'^(?:всю\s+серию|все\s+повторения)\s+',
            '', reference
        )
        month = (
            r'январ\w*|феврал\w*|март\w*|апрел\w*|мая|мае|'
            r'июн\w*|июл\w*|август\w*|сентябр\w*|'
            r'октябр\w*|ноябр\w*|декабр\w*'
        )
        weekday = (
            r'понедельн\w*|вторник\w*|сред[ауы]|четверг\w*|'
            r'пятниц\w*|суббот\w*|воскресен\w*'
        )
        day = (
            r'сегодня|завтра|послезавтра|'
            r'(?:(?:следующ\w*|эт\w*)\s+)?(?:' + weekday + r')|'
            r'следующ\w*\s+недел\w*|'
            r'\d{1,2}\s+(?:' + month + r')|'
            r'\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|'
            r'\d{1,2}:\d{2}'
        )
        # Убираем только очевидные обстоятельства времени после названия.
        reference = re.split(
            r'\s+(?:(?:на|в|во|к|до)\s+(?=(?:' + day + r')\b)'
            r'|(?=через\s))',
            reference, maxsplit=1
        )[0]
        reference = re.sub(r'\s+(?:сегодня|завтра|послезавтра)$',
                           '', reference).strip(' ,.;:')
        return _strip_temporal(reference)

    def _clock_of(self, user, item_id):
        row = self.crud.get_item(user, item_id)
        value = (row.get('start_at') or row.get('due_at')) if row else None
        if not value or 'T' not in value:
            return None
        moment = datetime.fromisoformat(value).astimezone(self.zone)
        return moment.hour, moment.minute

    def _search(self, user, plan, text, now):
        """Поиск по названию с учётом дня и часа из той же фразы.

        «Встреча с Иваном завтра в 10 часов дня» ищет «Иван» среди встреч
        на завтра в 10:00, а не все записи со словом «завтра».
        """
        raw = plan.get('query') if isinstance(plan.get('query'), str) else ''
        query = _strip_temporal(raw or text) or _strip_temporal(text)
        try:
            period = self.cal.interval(text, now)
        except DateError:
            period = None
        try:
            clocks = {self.cal.clock(text)} - {None}
        except DateError:
            hour = self._hour_hint(text)
            clocks = {(hour % 12, 0), (hour % 12 + 12, 0)} if hour else set()

        rows = self.crud.search_items(user, query) if query else []
        kind = _kind_from_text(text)
        if kind and any(x['type'] == kind for x in rows):
            rows = [x for x in rows if x['type'] == kind]

        when = ''
        if period:
            begin = datetime.combine(period[0], time.min, self.zone)
            finish = datetime.combine(period[1], time.max, self.zone)
            kept = []
            for row in rows:
                hits = _occurrences(row, begin, finish, self.zone)
                if clocks:
                    hits = [x for x in hits
                            if (x.hour, x.minute) in clocks
                            and 'T' in (row.get('start_at') or row.get('due_at') or '')]
                if hits:
                    kept.append(row)
            rows = kept
            when = (
                f'на {period[0].strftime("%d.%m")}'
                if period[0] == period[1]
                else f'с {period[0].strftime("%d.%m")} по {period[1].strftime("%d.%m")}'
            )
            if clocks:
                first = min(clocks)
                when += f' в {first[0]:02d}:{first[1]:02d}'
        return rows, query, when

    @staticmethod
    def _is_generic_reference(reference):
        """Не выбираем последнюю запись только из-за слова «встречу»."""
        generic = {
            '', 'запись', 'записи', 'эту запись', 'последнюю запись',
            'встречу', 'эту встречу', 'последнюю встречу',
            'задачу', 'эту задачу', 'последнюю задачу',
            'напоминание', 'это напоминание', 'последнее напоминание',
            'ее', 'её', 'его', 'это', 'эту', 'последнюю',
        }
        return norm(reference) in generic

    def _resolve_target(self, user, state, plan, text, now, action):
        """Одна безопасная процедура поиска для update и delete."""
        s = norm(text)
        reference = norm(plan.get('reference') or '')
        # Модель иногда кладёт имя записи в title вместо reference
        # («Отметь задачу купить билеты выполненной»). Берём его, только если
        # пользователь сам произнёс эти слова: «не указано» и т. п. не годится.
        title = norm(plan.get('title') or '') if isinstance(plan.get('title'), str) else ''
        if not reference and title and title in s:
            reference = title
        from_text = self._reference_from_command(text)
        explicit_kind = _kind_from_text(text)

        # Не выбираем разрушительную операцию по неоднозначному часу.
        if re.search(
            r'\b(?:в|на)\s+(?:один|два|две|три|четыре|пять|'
            r'шесть|семь|восемь|девять|десять|одиннадцать|'
            r'двенадцать)\s+час\w*\b'
            r'(?!\s+(?:утра|дня|вечера|ночи))',
            s
        ):
            raise UserInputError(
                'Уточни время в формате ЧЧ:ММ, например 15:00. '
                'Ничего не изменено.'
            )

        # Явный ID всегда проверяется в пределах конкретного пользователя.
        m = re.search(r'(?:#|\bid\s*)(\d+)\b', s + ' ' + reference)
        if m:
            row = self.crud.get_item(user, int(m[1]))
            if not row:
                raise UserInputError('Запись с этим ID не найдена.')
            if explicit_kind and row['type'] != explicit_kind:
                raise UserInputError('Тип записи не совпадает. Ничего не изменено.')
            return row

        # Для удаления дата — фильтр ИСХОДНЫХ записей.
        # При переносе дата указывает НОВОЕ время, фильтровать ею нельзя.
        on_day = None
        on_day_ids = None
        if action == 'delete':
            loose_hour = None
            try:
                on_day = self.cal.point(
                    text, now,
                    favorite_month=state.get('favorite_month')
                )
            except DateError:
                # «Завтра в 10 часов» для существующей записи: день известен,
                # а 10:00 или 22:00 — решит то, что есть в календаре.
                on_day = self._recover_day(text, now)
                loose_hour = self._hour_hint(text) if on_day else None
            if on_day:
                day = date.fromisoformat(on_day[:10])
                begin = datetime.combine(day, time.min, self.zone)
                finish = datetime.combine(day, time.max, self.zone)
                on_day_ids = {
                    row['id'] for row in self.crud.list_items(
                        user, day.isoformat(), day.isoformat(), explicit_kind
                    )
                    if _occurrences(row, begin, finish, self.zone)
                }

                # Уточнение часов относится к исходной встрече.
                # 15:00 и 15:30, 10:30 и 22:30 — разные записи.
                try:
                    target_clock = self.cal.clock(text)
                except DateError:
                    target_clock = None
                if target_clock is None and loose_hour is not None:
                    clocks = {(loose_hour % 12, 0), (loose_hour % 12 + 12, 0)}
                    on_day_ids = {
                        item_id for item_id in on_day_ids
                        if self._clock_of(user, item_id) in clocks
                    }
                if target_clock is not None:
                    matching_time_ids = set()
                    for item_id in on_day_ids:
                        row = self.crud.get_item(user, item_id)
                        value = (row.get('start_at') or row.get('due_at')) if row else None
                        if value and 'T' in value:
                            value_dt = datetime.fromisoformat(value).astimezone(self.zone)
                            if (value_dt.hour, value_dt.minute) == target_clock:
                                matching_time_ids.add(item_id)
                    on_day_ids = matching_time_ids

        # Приоритет имеет фактически произнесённое название,
        # а не неполный reference, возвращённый языковой моделью.
        queries = []
        if from_text and not self._is_generic_reference(from_text):
            queries.append(from_text)
        elif reference and not self._is_generic_reference(reference):
            queries.append(reference)

        # Если модель переименовала цель или вернула мусор, команда
        # пользователя всё равно считается основным источником.
        for query in queries:
            from .search_helpers import score
            matches = self.crud.search_items(user, query)
            matches = [
                row for row in matches
                if (not explicit_kind or row['type'] == explicit_kind)
                and (on_day_ids is None or row['id'] in on_day_ids)
            ]
            if not matches:
                continue
            values = [(score(query, row.get('title') or ''), row)
                      for row in matches]
            top_score = max(value for value, _ in values)
            # Недостаточно точное совпадение нельзя выбирать автоматически.
            if top_score < 0.60:
                continue
            strongest = [row for value, row in values
                         if value >= top_score - 0.001]
            if len(strongest) == 1:
                return strongest[0]
            raise AmbiguousTarget(strongest)

        # Явно указанное название не найдено: НЕ заменяем его
        # последней записью и не выбираем произвольную запись по дате.
        if queries:
            raise UserInputError(
                'Не нашла запись с таким названием. '
                'Уточни название или найди её через поиск. '
                'Ничего не изменено.'
            )

        # Только для настоящих местоимений допустим контекст.
        pronouns = {
            'ее', 'её', 'его', 'это', 'эту', 'последнюю',
            'эту запись', 'последнюю запись',
            'эту встречу', 'последнюю встречу',
            'эту задачу', 'последнюю задачу',
            'это напоминание', 'последнее напоминание'
        }
        # Голое «удали» или «перенеси на послезавтра» — про последнюю запись
        # из разговора, если назвать её было нечем.
        bare = not from_text and (not reference or reference in pronouns)
        if from_text in pronouns or bare:
            last = state.get('last_item_id')
            row = self.crud.get_item(user, last) if last else None
            if row and (not explicit_kind or row['type'] == explicit_kind):
                if on_day_ids is None or row['id'] in on_day_ids:
                    return row

        # «Удали встречу завтра» — без названия можно выбрать
        # единственную встречу за день, но не первую из нескольких.
        if on_day_ids is not None:
            candidates = [
                self.crud.get_item(user, item_id)
                for item_id in sorted(on_day_ids)
            ]
            candidates = [x for x in candidates if x]
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise AmbiguousTarget(candidates)
            raise UserInputError('За эту дату подходящих записей нет.')

        raise UserInputError(
            'Назови конкретную запись или её ID. '
            'Ничего не изменено.'
        )

    def _apply_update(
        self, user, state, row, plan, text, now
    ):
        if (
            row.get('recurrence')
            and not re.search(
                r'\b(?:всю\s+серию|все\s+повторения|'
                r'всех\s+встреч\w*)\b',
                norm(text)
            )
        ):
            raise UserInputError(
                'Это повторяющаяся запись. Уточни «всю серию». '
                'Изменение отдельного экземпляра пока не '
                'поддерживается; ничего не изменено.'
            )

        changes = {}

        if plan.get('new_title'):
            new_title = str(
                plan['new_title']
            )[:250].strip()

            if new_title:
                changes['title'] = new_title

        if plan.get('status') in (
            'done', 'pending'
        ):
            changes['status'] = plan['status']

        if (
            plan.get('_reschedule')
            or re.search(
                r'\b(?:перенес\w*|передвин\w*|'
                r'сдвин\w*|перепланир\w*)\b',
                norm(text)
            )
        ):
            base = (
                row.get('start_at')
                or row.get('due_at')
            )

            try:
                updated = self.cal.point(
                    plan.get('temporal_text') or text,
                    now,
                    favorite_month=state.get(
                        'favorite_month'
                    ),
                    base=base
                )
            except DateError as e:
                raise UserInputError(
                    str(e)
                ) from e

            if not updated:
                raise UserInputError(
                    'На какую дату перенести запись?'
                )

            # «Перенеси на послезавтра» меняет день, а не время:
            # у записи на 09:00 время сохраняется.
            if (
                len(updated) == 10
                and base and 'T' in base
                and not re.search(
                    r'\bбез\s+времени\b|\bна\s+весь\s+день\b',
                    norm(text)
                )
            ):
                clock = datetime.fromisoformat(base).astimezone(
                    self.zone
                )
                updated = datetime.combine(
                    date.fromisoformat(updated),
                    time(clock.hour, clock.minute),
                    self.zone
                ).isoformat(timespec='seconds')

            if (
                row.get('type') == 'meeting'
                and len(updated) == 10
            ):
                raise UserInputError(
                    'Уточни время встречи.'
                )

            if row.get('recurrence'):
                raise UserInputError(
                    'Уточни изменение всей серии или отдельной встречи. '
                    'Автоматически не меняю.'
                )

            if (
                'T' in updated
                and datetime.fromisoformat(updated) <= now
            ):
                raise UserInputError(
                    'Нельзя перенести событие в прошлое.'
                )

            changes[
                'start_at'
                if row['type'] == 'meeting'
                else 'due_at'
            ] = updated

            if (
                row['type'] == 'meeting'
                and self._conflicts(
                    user,
                    {**row, **changes},
                    ignore=row['id']
                )
            ):
                raise UserInputError(
                    'Новая встреча пересечётся с другой. '
                    'Уточни время.'
                )

        if not changes:
            raise UserInputError(
                'Что именно нужно изменить?'
            )

        saved = self.crud.update_item(
            user, row['id'], changes
        )

        state['last_item_id'] = row['id']

        return _response(
            'success',
            f"Обновлено: «{saved['title']}» — "
            f"{_human(saved.get('start_at') or saved.get('due_at'), self.zone)}.",
            'update',
            saved
        )

    def _bulk_delete_request(self, user, state, text, now):
        """Массовое удаление записей за один день."""
        s = norm(text)

        if not re.match(
            r'^(?:удали|удалить|убери|очисти|очистить|сотри)\s+'
            r'(?:(?:все|всё|весь|всю)\b(?!\s+(?:серию|повторения))|'
            r'(?:список|календарь|расписание|записи)\b)',
            s
        ):
            return None

        safe_way = _response(
            'clarification',
            'Удалить сразу всё не могу: так легко потерять нужное. '
            'Могу удалить все записи за один день, например '
            '«удали все записи на завтра». Ничего не удалено.',
            'delete',
            suggestions=[
                'Удали все записи на сегодня',
                'Удали все записи на завтра',
            ]
        )

        # Не угадываем дату и не выбираем последнюю запись.
        if re.search(r'\b(?:до|после|перед)\b', s):
            return safe_way

        try:
            period = self.cal.interval(text, now)
        except DateError:
            period = None

        if not period or period[0] != period[1]:
            return safe_way

        day = period[0].isoformat()
        kind = _kind_from_text(text)

        begin = datetime.combine(
            period[0], time.min, self.zone
        )
        finish = datetime.combine(
            period[0], time.max, self.zone
        )

        items = [
            x for x in self.crud.list_items(user, day, day, kind)
            if _occurrences(x, begin, finish, self.zone)
        ]

        if not items:
            return _response(
                'success',
                'На указанную дату подходящих записей нет. '
                'Ничего не удалено.',
                'delete'
            )

        # Не удаляем целые повторяющиеся серии случайно.
        if any(x.get('recurrence') for x in items):
            return _response(
                'clarification',
                'На эту дату есть повторяющиеся записи. '
                'Массово удалять целые серии нельзя; '
                'ничего не удалено.',
                'delete'
            )

        if len(items) > 30:
            return _response(
                'clarification',
                'Найдено больше 30 записей. '
                'Уточни удаление по типу; ничего не удалено.',
                'delete'
            )

        state['pending'] = {
            'need': 'delete_many',
            'day': day,
            'kind': kind,
            'target_ids': [x['id'] for x in items]
        }

        preview = '; '.join(
            f"«{x['title']}»" for x in items[:5]
        )

        if len(items) > 5:
            preview += f'; и ещё {len(items) - 5}'

        return _response(
            'confirmation_required',
            f'Удалить все записи за '
            f'{period[0].strftime("%d.%m.%Y")} '
            f'(всего {len(items)})? '
            f'{preview}. '
            'Ответь «да» для подтверждения '
            'или «нет» для отмены.',
            'delete'
        )

    def _continue(
        self, user, state, text, now
    ):
        p = state.get('pending')

        if not p:
            return None

        s = norm(text)

        # Подтверждение массового удаления обрабатываем ДО новых команд.
        if p.get('need') == 'delete_many':
            if s in {'нет', 'отмена', 'отмени', 'не надо', 'забудь'}:
                state['pending'] = None
                return _response(
                    'cancelled',
                    'Массовое удаление отменено. Ничего не изменено.'
                )

            if s in {'да', 'подтверждаю', 'подтверждаю удаление'}:
                day = p['day']
                d = date.fromisoformat(day)
                begin = datetime.combine(d, time.min, self.zone)
                finish = datetime.combine(d, time.max, self.zone)

                # Нельзя удалять другой набор, если записи изменились.
                items = [
                    x for x in self.crud.list_items(
                        user, day, day, p.get('kind')
                    )
                    if _occurrences(x, begin, finish, self.zone)
                ]
                actual_ids = sorted(x['id'] for x in items)
                expected_ids = sorted(p['target_ids'])
                if (
                    actual_ids != expected_ids
                    or any(x.get('recurrence') for x in items)
                ):
                    state['pending'] = None
                    return _response(
                        'clarification',
                        'Список записей изменился. Ничего не удалено. '
                        'Повтори команду.',
                        'delete'
                    )

                removed = 0
                for row in items:
                    if self.crud.delete_item(user, row['id']):
                        removed += 1

                state['pending'] = None
                state['last_item_id'] = None
                return _response(
                    'success',
                    f'Удалено записей за {d.strftime("%d.%m.%Y")}: '
                    f'{removed}.',
                    'delete'
                )

            if re.match(
                r'^(?:добавь|создай|поставь|назначь|запиши|'
                r'напомни|найди|покажи|перенеси|удали|убери|'
                r'очисти|что\s+у\s+меня|какие\s+планы)\b',
                s
            ):
                state['pending'] = None
                return None

            return _response(
                'confirmation_required',
                'Чтобы удалить выбранные записи, ответь «да». '
                'Чтобы отменить — «нет».',
                'delete'
            )

        # Новая команда отменяет старое незавершённое действие.
        # Одиночные «да», «нет», «отмена» остаются ответами.
        if re.match(
            r'^(?:добавь|создай|поставь|назначь|'
            r'запиши|напомни|найди|покажи|'
            r'перенеси|удали|что\s+у\s+меня|'
            r'какие\s+планы|'
            r'отмени\s+(?:встреч\w*|запис\w*|'
            r'задач\w*|напоминан\w*))\b',
            s
        ):
            state['pending'] = None
            return None

        if s in {
            'отмена', 'отмени', 'нет',
            'не надо', 'забудь'
        }:
            state['pending'] = None

            return _response(
                'cancelled',
                'Хорошо, действие отменено.'
            )

        if p.get('need') == 'kind':
            kind = _kind_from_text(text)
            if kind is None:
                kind = {
                    'встреча': 'meeting', 'встречу': 'meeting',
                    'напоминание': 'reminder', 'напомни': 'reminder',
                    'задача': 'task', 'задачу': 'task', 'дело': 'task',
                }.get(s)
            state['pending'] = None
            if kind is None:
                # Не ответ на вопрос, а новая фраза: обрабатываем её заново.
                return None
            state['_replay'] = {
                'plan': {**p['plan'], 'kind': kind},
                'text': p['original_text'],
                'kind': kind,
            }
            return None

        if p.get('need') == 'choose_target':
            if re.match(
                r'^(?:поставь|создай|найди|покажи|'
                r'напомни|удали|перенеси)\b',
                s
            ):
                state['pending'] = None
                return None

            m = re.fullmatch(
                r'(?:номер\s*)?(\d+)(?:-?[йяую])?',
                s
            )

            index = int(m[1]) if m else None

            words = {
                'первую': 1,
                'первый': 1,
                'первая': 1,
                'вторую': 2,
                'второй': 2,
                'вторая': 2,
                'третью': 3,
                'третий': 3,
                'третья': 3,
                'четвертую': 4,
                'четвертый': 4,
                'пятую': 5,
                'пятый': 5
            }

            index = index or words.get(s)

            if (
                index is None
                or not 1 <= index <= len(
                    p['target_ids']
                )
            ):
                return _response(
                    'clarification',
                    f"Выбери номер от 1 до {len(p['target_ids'])} "
                    "или скажи «отмена».",
                    p['action'],
                    suggestions=[
                        str(i + 1) for i in range(len(p['target_ids']))
                    ]
                )

            row = self.crud.get_item(
                user,
                p['target_ids'][index - 1]
            )

            if not row:
                state['pending'] = None
                raise UserInputError(
                    'Эта запись уже недоступна; '
                    'ничего не изменено.'
                )

            if p['action'] == 'delete':
                if (
                    row.get('recurrence')
                    and not re.search(
                        r'\b(?:всю\s+серию|все\s+повторения)\b',
                        norm(p['original_text'])
                    )
                ):
                    state['pending'] = None

                    return _response(
                        'clarification',
                        'Это повторяющаяся встреча. '
                        'Чтобы удалить все экземпляры, '
                        'скажи «удали всю серию». '
                        'Отдельные экземпляры пока не удаляю.',
                        'delete'
                    )

                state['pending'] = {
                    'need': 'delete',
                    'target_id': row['id'],
                    'scope': (
                        'series'
                        if row.get('recurrence')
                        else 'single'
                    )
                }

                return _response(
                    'confirmation_required',
                    f"Удалить «{row['title']}» на "
                    f"{_human(row.get('start_at') or row.get('due_at'), self.zone)}?",
                    'delete',
                    row
                )

            state['pending'] = None

            return self._apply_update(
                user,
                state,
                row,
                p['plan'],
                p['original_text'],
                now
            )

        if p['need'] in (
            'conflict', 'duplicate'
        ):
            if s in {
                'да', 'давай', 'сохрани',
                'подтверждаю', 'все равно'
            }:
                return self._commit_create(
                    user, state, p, now,
                    confirmed=True
                )

            return _response(
                'confirmation_required',
                'Ответь «да» или «нет».',
                p.get('action')
            )

        if p['need'] == 'delete':
            if s in {
                'да', 'удали', 'подтверждаю',
                'подтверждаю удаление'
            }:
                row = self.crud.get_item(
                    user,
                    p['target_id']
                )

                if (
                    row
                    and row.get('recurrence')
                    and p.get('scope') != 'series'
                ):
                    state['pending'] = None

                    return _response(
                        'clarification',
                        'Удаление всей серии не подтверждено. '
                        'Ничего не изменено.',
                        'delete'
                    )

                if row:
                    self.crud.delete_item(
                        user,
                        p['target_id']
                    )

                state['pending'] = None
                state['last_item_id'] = None

                return _response(
                    'success',
                    (
                        'Запись удалена.'
                        if row
                        else 'Запись уже отсутствует.'
                    ),
                    'delete',
                    row
                )

            return _response(
                'confirmation_required',
                'Подтверди удаление или напиши «отмена».',
                'delete'
            )

        if p.get('need') == 'weekday':
            weekday = next(
                (
                    d
                    for stem, d in DAYS.items()
                    if re.search(
                        r'\b' + stem + r'\w*',
                        s
                    )
                ),
                None
            )

            if weekday is None:
                return _response(
                    'clarification',
                    'Назови день недели: понедельник, '
                    'вторник и так далее.',
                    'create'
                )

            p['recurrence']['by_weekday'] = [weekday]
            p['at'] = None
            p['need'] = None

            # Original request retains specified time.
            return self._commit_create(
                user, state, p, now
            )

        if p.get('need') == 'time' and p.get('at'):
            # Собираем время из нескольких сообщений.
            part = self._daypart_hint(text)

            if part:
                p['daypart_hint'] = part

            hour = self._hour_hint(text)

            if hour is not None:
                p['hour_hint'] = hour

            try:
                clock = self.cal.clock(
                    text, short=True
                )
            except DateError:
                clock = None

            # «На шесть» + «вечером» -> 18:00.
            if (
                clock is None
                and p.get('hour_hint')
                and p.get('daypart_hint')
            ):
                clock = self.cal.clock(
                    f"в {p['hour_hint']} {p['daypart_hint']}",
                    short=True
                )

            if clock is not None:
                p['at'] = self.cal._dt(
                    date.fromisoformat(
                        p['at'][:10]
                    ),
                    clock
                )

                p['need'] = None

                return self._commit_create(
                    user, state, p, now
                )

            if p.get('hour_hint'):
                return _response(
                    'clarification',
                    f"{p['hour_hint']:02d}:00 утра или вечера?",
                    'create',
                    suggestions=['Утра', 'Вечера']
                )

            if p.get('daypart_hint'):
                question = (
                    'На какое время вечером?'
                    if p['daypart_hint'] == 'вечера'
                    else 'На какое время?'
                )

                return _response(
                    'clarification',
                    question,
                    'create'
                )

        if p['need'] == 'favorite_month':
            month = month_from_text(text)

            if not month:
                return _response(
                    'clarification',
                    'Назови месяц, например «сентябрь».',
                    'create'
                )

            state['favorite_month'] = month
            text = p['temporal_text']

        elif re.search(
            r'^(?:поставь|создай|напомни|найди|'
            r'покажи|удали|назначь|запиши|'
            r'что у меня|какие)\b',
            s
        ):
            state['pending'] = None
            return None

        try:
            if p.get('next_week'):
                weekday = next(
                    (
                        d
                        for stem, d in DAYS.items()
                        if re.search(
                            r'\b' + stem + r'\w*\b',
                            norm(text)
                        )
                    ),
                    None
                )

                if weekday is not None:
                    monday = (
                        now.date()
                        - timedelta(
                            days=now.weekday()
                        )
                        + timedelta(days=7)
                    )

                    day = monday + timedelta(
                        days=weekday
                    )

                    clock = self.cal.clock(text)

                    point = (
                        self.cal._dt(day, clock)
                        if clock
                        else day.isoformat()
                    )

                    p['next_week'] = False

                else:
                    point = self.cal.point(
                        text,
                        now,
                        favorite_month=state.get(
                            'favorite_month'
                        ),
                        base=p.get('at')
                    )
            else:
                point = self.cal.point(
                    text,
                    now,
                    favorite_month=state.get(
                        'favorite_month'
                    ),
                    base=p.get('at')
                )

        except DateError as e:
            if 'любимый месяц' in str(e):
                p['need'] = 'favorite_month'

            return _response(
                'clarification',
                str(e),
                'create'
            )

        if point:
            p['at'] = point

            if (
                len(point) == 10
                and p.get('hour_hint')
            ):
                p['need'] = 'time'

                return _response(
                    'clarification',
                    f"Уточни, {p['hour_hint']:02d}:00 утра или вечера?",
                    'create',
                    suggestions=['Утра', 'Вечера']
                )

        elif not p.get('at'):
            return _response(
                'clarification',
                'На какую дату поставить запись?',
                'create',
                suggestions=['Сегодня', 'Завтра', 'Послезавтра']
            )

        return self._commit_create(
            user, state, p, now
        )

    def handle_message(
        self,
        text: str,
        user_id='demo',
        session_id='default'
    ):
        if not isinstance(text, str) or not text.strip():
            return _response(
                'error',
                'Напиши сообщение.'
            )

        said = text

        if len(text) > 2000:
            return _response(
                'error',
                'Сообщение слишком длинное.'
            )

        now = self.now_func().astimezone(
            self.zone
        )

        state = (
            self.crud.get_session(
                user_id, session_id
            )
            or {}
        )

        state.setdefault('pending', None)
        state.setdefault('last_item_id', None)
        state.setdefault('favorite_month', None)
        state.setdefault('history', [])

        try:
            # Conversation continuation should not require an LLM request.
            answer = self._continue(
                user_id, state, text, now
            )

            if answer is not None:
                return self._reply(
                    user_id, session_id,
                    state, text, answer
                )

            replay = state.pop('_replay', None)

            # Перехватываем массовое удаление до LLM.
            bulk = None if replay else self._bulk_delete_request(
                user_id, state, text, now
            )
            if bulk is not None:
                return self._reply(
                    user_id, session_id,
                    state, text, bulk
                )

            correction = None if replay else self._implicit_time_correction(
                user_id, state, text, now
            )

            if correction is not None:
                return self._reply(
                    user_id, session_id,
                    state, text, correction
                )

            # Known preference update; no calendar action.
            pref = re.search(
                r'(?:мой\s+любим\w*\s+месяц\s*'
                r'(?:это|—|-|:)\s*)([а-яё]+)',
                norm(text)
            )

            if pref and not replay:
                month = month_from_text(
                    pref[1]
                )

                if month:
                    state['favorite_month'] = month

                    return self._reply(
                        user_id,
                        session_id,
                        state,
                        text,
                        _response(
                            'success',
                            'Хорошо, буду учитывать этот месяц.',
                            'preference'
                        )
                    )

            everything = self.crud.list_items(
                user_id,
                '1900-01-01',
                '2100-12-31'
            )

            # В LLM уходят 100 самых свежих записей, новые первыми:
            # промпт обрезается с конца, а свежие записи нужнее.
            catalog = [
                {
                    k: x.get(k) for k in (
                        'id', 'type', 'title', 'start_at',
                        'due_at', 'recurrence', 'status'
                    )
                }
                for x in sorted(
                    everything,
                    key=lambda x: x['id'],
                    reverse=True
                )[:100]
            ]

            if replay:
                # Ответ на «задача, встреча или напоминание?»: повторяем
                # исходный запрос с известным типом, без нового вызова LLM.
                plan = replay['plan']
                text = replay['text']
            else:
                plan = self.planner.plan(
                    text,
                    {
                        'last_item_id': state['last_item_id'],
                        'pending': None,
                        'history': state['history']
                    },
                    now,
                    catalog
                )

            if not isinstance(plan, dict):
                raise UserInputError(
                    'Не получилось понять запрос.'
                )

            action = plan.get(
                'action', 'unknown'
            )

            s = norm(text)

            # Явные команды пользователя имеют приоритет над ошибкой LLM.
            if re.match(r'^(?:удали|удалить|убери|сотри|стереть)\b', s):
                action = 'delete'
            elif re.match(
                r'^(?:перенеси|перенести|передвинь|передвинуть|'
                r'сдвинь|сдвинуть|перепланируй)\b', s
            ):
                action = 'update'
                plan = {**plan, '_reschedule': True,
                        'temporal_text': text, 'new_title': None}

            # Вопрос о расписании на конкретную дату
            # должен показывать календарные записи,
            # даже если модель выбрала семантический поиск.
            if (
                re.search(
                    r'\b(?:что\s+у\s+меня|'
                    r'какие\s+планы|'
                    r'что\s+запланировано|'
                    r'что\s+запланировано\s+на)\b',
                    s
                )
                and self.cal.interval(
                    text, now
                )
            ):
                if action in {
                    'search', 'list', 'answer', 'unknown'
                }:
                    action = 'list'

                    plan = {
                        **plan,
                        'temporal_text': text,
                        'kind': (
                            _kind_from_text(text)
                            or 'any'
                        )
                    }

            if (
                plan.get('negated')
                or re.search(
                    r'\bне\s+(?:создавай|добавляй|'
                    r'записывай)\b',
                    s
                )
            ):
                result = _response(
                    'success',
                    'Ничего не создаю.',
                    'answer'
                )

            elif action == 'create':
                kind = (
                    (replay or {}).get('kind')
                    or _kind_from_text(text)
                    or plan.get('kind')
                )

                # Модель любит называть встречей любое дело с датой:
                # «Купить подарок маме в пятницу» — это задача.
                if (
                    kind == 'meeting'
                    and not replay
                    and not _kind_from_text(text)
                    and not re.search(
                        r'\b(?:совещан|планерк|собеседован|переговор|'
                        r'конференц|стендап|митинг|созвон|звонок\s+с|'
                        r'свидан|приём|прием|встрет)\w*',
                        s
                    )
                    and not re.search(r'\bс\s+[А-ЯЁA-Z]', text)
                ):
                    kind = 'task'

                if kind not in {
                    'task', 'meeting', 'reminder'
                }:
                    # Запоминаем запрос целиком: ответ «задача» достроит его
                    # без повторного обращения к модели.
                    state['pending'] = {
                        'action': 'create',
                        'need': 'kind',
                        'plan': plan,
                        'original_text': text,
                    }
                    raise ClarifyKind()

                title = plan.get('title')

                if (
                    not isinstance(title, str)
                    or norm(title) in {
                        '', 'задача', 'встреча',
                        'напоминание', 'null'
                    }
                ):
                    # Выражение времени, выделенное моделью, в название не входит:
                    # «Напомни через два часа выпить таблетку» → «Выпить таблетку».
                    source = text
                    said = plan.get('temporal_text')
                    if isinstance(said, str) and said.strip():
                        source = re.sub(
                            re.escape(said.strip()), ' ', text,
                            count=1, flags=re.I
                        )
                    title = _fallback_title(
                        source, kind
                    )

                title = title.strip()
                title = title[:1].upper() + title[1:]

                temporal = plan.get(
                    'temporal_text'
                )

                # Original wording is authoritative:
                # a model can drop important dates.
                if has_temporal_words(text):
                    temporal = text

                elif (
                    not isinstance(temporal, str)
                    or norm(temporal) in (
                        '', 'null', 'none'
                    )
                ):
                    temporal = text

                if re.search(
                    r'\b(?:перв\w*|втор\w*|'
                    r'последн\w*)\s+'
                    r'(?:понедельн|вторник|сред|'
                    r'четверг|пятниц|суббот|'
                    r'воскресен)\w*'
                    r'.*\bмесяц\w*',
                    norm(text)
                ):
                    raise UserInputError(
                        'Такое правило повторения пока '
                        'не поддерживается; '
                        'запись не создана.'
                    )

                recurrence = _validate_recurrence(
                    _recurrence_from_text(text)
                    or plan.get('recurrence')
                )

                duration = (
                    duration_minutes(
                        text,
                        plan.get(
                            'duration_minutes'
                        )
                    )
                    if kind == 'meeting'
                    else 60
                )

                if kind == 'meeting':
                    date_range = numeric_range(
                        temporal
                    )

                    if date_range:
                        temporal, duration = date_range

                try:
                    at = self.cal.point(
                        temporal,
                        now,
                        favorite_month=state.get(
                            'favorite_month'
                        )
                    )

                except DateError as e:
                    need = (
                        'favorite_month'
                        if 'любимый месяц' in str(e)
                        else 'date'
                    )

                    day_hint = (
                        self._recover_day(
                            temporal, now
                        )
                        if need != 'favorite_month'
                        else None
                    )

                    hour_hint = self._hour_hint(
                        temporal
                    )

                    # Keep a known date even when only
                    # the 12-hour clock is ambiguous.
                    if day_hint:
                        need = (
                            'time'
                            if hour_hint is not None
                            else 'date'
                        )

                    state['pending'] = {
                        'action': 'create',
                        'need': need,
                        'type': kind,
                        'title': title,
                        'temporal_text': temporal,
                        'at': day_hint,
                        'hour_hint': hour_hint,
                        'daypart_hint': self._daypart_hint(
                            temporal
                        ),
                        'recurrence': recurrence,
                        'duration_minutes': duration,
                        'date_required': True,
                        'request_id': str(
                            uuid.uuid4()
                        )
                    }

                    if (
                        day_hint
                        and hour_hint is not None
                    ):
                        message = (
                            f'Уточни, {hour_hint:02d}:00 '
                            'утра или вечера?'
                        )

                    elif (
                        day_hint
                        and kind == 'meeting'
                    ):
                        message = (
                            'На какое время назначить встречу?'
                        )

                    else:
                        message = str(e)

                    suggestions = None
                    if day_hint and hour_hint is not None:
                        suggestions = ['Утра', 'Вечера']
                    elif day_hint and kind == 'meeting':
                        suggestions = ['В 10:00', 'В 15:00', 'В 18:00']
                    elif need == 'date':
                        suggestions = ['Сегодня', 'Завтра', 'Послезавтра']

                    result = _response(
                        'clarification',
                        message,
                        'create',
                        suggestions=suggestions
                    )

                else:
                    next_week = bool(
                        re.search(
                            r'\b(?:на|в)\s+'
                            r'следующ\w*\s+недел\w*',
                            norm(text)
                        )
                    )

                    pending = {
                        'action': 'create',
                        'need': None,
                        'type': kind,
                        'title': title,
                        'temporal_text': temporal,
                        'at': at,
                        'recurrence': recurrence,
                        'duration_minutes': duration,
                        'date_required': has_temporal_words(
                            text
                        ),
                        'request_id': str(
                            uuid.uuid4()
                        ),
                        'hour_hint': self._hour_hint(
                            temporal
                        ),
                        'daypart_hint': self._daypart_hint(
                            temporal
                        ),
                        'next_week': (
                            next_week and not at
                        )
                    }

                    if next_week and not at:
                        pending['need'] = 'date'
                        state['pending'] = pending

                        result = _response(
                            'clarification',
                            'На какой день следующей недели? '
                            'Например, в пятницу.',
                            'create'
                        )

                    else:
                        result = self._commit_create(
                            user_id, state,
                            pending, now
                        )

            elif action == 'list':
                window = self._clock_window(text)
                if 'без срока' in s:
                    rows = [
                        x for x in everything
                        if not (
                            x.get('start_at')
                            or x.get('due_at')
                        )
                    ]

                else:
                    period = self.cal.interval(
                        plan.get(
                            'temporal_text'
                        ) or text,
                        now
                    )

                    if not period and window:
                        # «Что у меня после 18:00?» — про сегодня.
                        period = (now.date(), now.date())

                    if not period:
                        raise UserInputError(
                            'За какой период показать записи?'
                        )

                    kind = (
                        plan.get('kind')
                        if plan.get('kind') in {
                            'task', 'meeting', 'reminder'
                        }
                        else None
                    )
                    # Тип фильтруем, только если его назвал пользователь:
                    # модель считает «дела» и «планы» задачами и прячет встречи.
                    kind_words = {'task': r'\bзадач', 'meeting': r'\bвстреч',
                                  'reminder': r'\bнапомина'}
                    if kind and not re.search(kind_words[kind], s):
                        kind = None

                    templates = self.crud.list_items(
                        user_id,
                        period[0].isoformat(),
                        period[1].isoformat(),
                        kind
                    )

                    begin = datetime.combine(
                        period[0],
                        time.min,
                        self.zone
                    )

                    finish = datetime.combine(
                        period[1],
                        time.max,
                        self.zone
                    )

                    rows = []

                    for item in templates:
                        for occurrence in _occurrences(
                            item,
                            begin,
                            finish,
                            self.zone
                        ):
                            rows.append({
                                **item,
                                'occurrence_at': (
                                    occurrence.date().isoformat()
                                    if len(
                                        item.get('start_at')
                                        or item.get('due_at')
                                        or ''
                                    ) == 10
                                    else occurrence.isoformat(
                                        timespec='seconds'
                                    )
                                )
                            })

                    rows = sorted(
                        rows,
                        key=lambda x: x['occurrence_at']
                    )[:100]

                    if window:
                        # Только записи со временем внутри окна [с, до).
                        start, end, _ = window
                        rows = [
                            x for x in rows
                            if 'T' in x['occurrence_at']
                            and start <= (
                                datetime.fromisoformat(x['occurrence_at']).hour * 60
                                + datetime.fromisoformat(x['occurrence_at']).minute
                            ) < end
                        ]

                    # Записи без срока открыты «сейчас»: показываем их
                    # отдельным блоком, если период включает сегодня.
                    if not window and period[0] <= now.date() <= period[1]:
                        rows += [
                            x for x in templates
                            if not (
                                x.get('start_at')
                                or x.get('due_at')
                            )
                            and x.get('status') != 'done'
                        ][:20]

                label = window[2] if window else None
                result = _response(
                    'success',
                    self._describe_list(rows, label),
                    'list',
                    items=rows,
                    speech_text=self._speak_list(rows, label)
                )

            elif action == 'search':
                rows, query, when = self._search(
                    user_id, plan, text, now
                )

                state['last_item_id'] = (
                    rows[0]['id']
                    if len(rows) == 1
                    else None
                )

                if rows:
                    result = _response(
                        'success',
                        self._describe_list(rows),
                        'search',
                        items=rows,
                        speech_text=self._speak_list(rows)
                    )
                else:
                    shown = query or norm(text)
                    message = (
                        f'Не нашла записей по запросу «{shown}»'
                        + (f' {when}' if when else '')
                        + '. Попробуй другое слово из названия '
                        'или посмотри список на неделю.'
                    )
                    result = _response(
                        'success',
                        message,
                        'search',
                        items=[],
                        suggestions=['Что на этой неделе?', 'Что у меня завтра?']
                    )

            elif action in ('update', 'delete'):
                row = self._resolve_target(
                    user_id,
                    state,
                    plan,
                    text,
                    now,
                    action
                )

                if action == 'delete':
                    if (
                        row.get('recurrence')
                        and not re.search(
                            r'\b(?:всю\s+серию|'
                            r'все\s+повторения)\b',
                            s
                        )
                    ):
                        result = _response(
                            'clarification',
                            'Это повторяющаяся запись. '
                            'Чтобы удалить все экземпляры, '
                            'скажи «удали всю серию». '
                            'Отдельные экземпляры пока '
                            'не удаляю.',
                            'delete'
                        )

                    else:
                        state['pending'] = {
                            'need': 'delete',
                            'target_id': row['id'],
                            'scope': (
                                'series'
                                if row.get('recurrence')
                                else 'single'
                            )
                        }
                        # После «нет» голое «удали» вернётся к этой записи.
                        state['last_item_id'] = row['id']

                        label = (
                            'ВСЮ СЕРИЮ '
                            if row.get('recurrence')
                            else ''
                        )

                        result = _response(
                            'confirmation_required',
                            f"Удалить {label}«{row['title']}» "
                            f"на {_human(row.get('start_at') or row.get('due_at'), self.zone)}?",
                            'delete',
                            row
                        )

                else:
                    result = self._apply_update(
                        user_id,
                        state,
                        row,
                        plan,
                        text,
                        now
                    )

            elif action == 'answer':
                if re.search(
                    r'\b(?:какая|какой)\s+'
                    r'(?:дата|день)\s+завтра\b',
                    s
                ):
                    result = _response(
                        'success',
                        f"Завтра {(now.date() + timedelta(days=1)).strftime('%d.%m.%Y')}.",
                        'answer'
                    )

                else:
                    result = _response(
                        'clarification',
                        'Могу помочь с задачами, '
                        'встречами и напоминаниями.',
                        'answer'
                    )

            else:
                result = _response(
                    'clarification',
                    'Уточни запрос, пожалуйста. '
                    'Ничего не изменено.',
                    'unknown'
                )

        except ClarifyKind:
            result = _response(
                'clarification',
                'Что это: задача, встреча или напоминание?',
                'create',
                suggestions=['Задача', 'Встреча', 'Напоминание']
            )

        except AmbiguousTarget as exc:
            state['pending'] = {
                'need': 'choose_target',
                'target_ids': [
                    x['id'] for x in exc.rows
                ],
                'action': action,
                'plan': plan,
                'original_text': text
            }

            choices = '; '.join(
                f"{i + 1}. «{x['title']}» — "
                f"{_human(x.get('start_at') or x.get('due_at'), self.zone)}"
                for i, x in enumerate(exc.rows)
            )

            result = _response(
                'clarification',
                'Подходят несколько записей: '
                + choices
                + '. Назови номер нужной. '
                'Ничего не изменено.',
                action,
                suggestions=[str(i + 1) for i in range(len(exc.rows))]
            )

        except (
            DateError,
            UserInputError,
            ValueError,
            KeyError,
            TypeError
        ) as exc:
            result = _response(
                'clarification',
                str(exc) or (
                    'Не удалось корректно обработать запрос.'
                )
            )

        except Exception:
            # Не показываем пользователю секреты или текст
            # внутренних исключений.
            result = _response(
                'error',
                'Не удалось связаться с языковой моделью '
                'или хранилищем. '
                'Повтори запрос позже.'
            )

        return self._reply(
            user_id,
            session_id,
            state,
            said,
            result
        )

    @staticmethod
    def _clock_window(text):
        """Окно времени суток из запроса списка: «после 19:30», «с 10 до 12»,
        «до 15:00», «утром», «вечером». Возвращает (с, до, подпись) в минутах
        от полуночи, окно [с, до). Явное «до 12:00» включает 12:00.
        None — фильтра нет."""
        s = norm(text)
        # Часы словами: «после семи вечера», «с девяти до одиннадцати».
        hours = {
            'часа': 1, 'одного': 1, 'двух': 2, 'два': 2, 'трех': 3, 'три': 3,
            'четырех': 4, 'четыре': 4, 'пяти': 5, 'пять': 5, 'шести': 6, 'шесть': 6,
            'семи': 7, 'семь': 7, 'восьми': 8, 'восемь': 8, 'девяти': 9, 'девять': 9,
            'десяти': 10, 'десять': 10, 'одиннадцати': 11, 'одиннадцать': 11,
            'двенадцати': 12, 'двенадцать': 12, 'полудня': 12,
        }
        s = re.sub(
            r'\b(после|позже|с|со|до|раньше|начиная\s+с)\s+(' + '|'.join(hours) + r')\b',
            lambda m: f'{m[1]} {hours[m[2]]}', s
        )
        not_date = (
            r'(?!\s*(?:числ\w*|январ\w*|феврал\w*|март\w*|апрел\w*|ма[яе]|'
            r'июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\b)'
            r'(?![./-]\d{1,2}[./-])'
        )
        clock = r'(\d{1,2})(?:[:.](\d{2}))?(?:\s*(утра|дня|вечера|ночи))?\b' + not_date

        def minutes(h, m, part):
            h, m = int(h), int(m or 0)
            if part == 'дня' and 1 <= h <= 8:
                h += 12
            if part == 'вечера' and h < 12:
                h += 12
            if part == 'ночи' and h == 12:
                h = 0
            return h * 60 + m if h < 24 and m < 60 else None

        def label(value):
            return f'{value // 60:02d}:{value % 60:02d}'

        start = end = None
        m = re.search(r'\b(?:после|позже|с|со|начиная\s+с)\s+' + clock, s)
        if m:
            start = minutes(*m.groups())
        m = re.search(r'\b(?:до|раньше|не\s+позже)\s+' + clock, s)
        if m:
            end = minutes(*m.groups())
        if start is not None or end is not None:
            if start is not None and end is not None:
                return start, end + 1, f'с {label(start)} до {label(end)}'
            if start is not None:
                return start, 24 * 60, f'после {label(start)}'
            return 0, end + 1, f'до {label(end)}'

        # «Что у меня завтра в 10:00?» — записи ровно на этот час.
        m = re.search(
            r'\b(?:в|на|к)\s+(\d{1,2})(?:[:.](\d{2}))?(?:\s+час\w*)?'
            r'(?:\s+(утра|дня|вечера|ночи))?\b' + not_date
            + r'(?!\s+(?:дн(?:я|ей)|недел\w*|месяц\w*))',
            s
        )
        if m and (m[2] or m[3] or re.search(r'\bчас', m[0])):
            at = minutes(*m.groups())
            if at is not None:
                return at, at + 1, f'в {label(at)}'

        parts = (
            (r'\bдо\s+обеда\b', 0, 13 * 60, 'до обеда'),
            (r'\bпосле\s+обеда\b', 13 * 60, 24 * 60, 'после обеда'),
            (r'\bутр(?:ом|а)\b|\bв\s+первой\s+половине\s+дня\b', 0, 12 * 60, 'утром'),
            (r'\bдн[её]м\b|\bво\s+второй\s+половине\s+дня\b', 12 * 60, 18 * 60, 'днём'),
            (r'\bвечер(?:ом|а)?\b', 18 * 60, 24 * 60, 'вечером'),
            (r'\bночью\b', 0, 6 * 60, 'ночью'),
        )
        for pattern, begin, finish, name in parts:
            if re.search(pattern, s):
                return begin, finish, name
        return None

    @staticmethod
    def _title_with_kind(item, spoken=False):
        """Название с типом, но без повтора: «Встреча с Иваном», а не
        «Встреча с Иваном (Встреча)»."""
        title = item.get('title') or 'Без названия'
        if spoken:  # в середине фразы — со строчной
            title = title[:1].lower() + title[1:]
        kind = item.get('type')
        stems = {'meeting': 'встреч', 'reminder': 'напомина', 'task': 'задач'}
        if kind not in stems or norm(title).startswith(stems[kind]):
            return title
        word = {'meeting': 'встреча', 'reminder': 'напоминание', 'task': 'задача'}[kind]
        return f'{word}: {title}' if spoken else f'{title} · {word}'

    @staticmethod
    def _when(item):
        value = item.get('occurrence_at') or item.get('start_at') or item.get('due_at')
        return value, (datetime.fromisoformat(value) if value and 'T' in value else None)

    def _speak_list(self, rows, window=None):
        """Текст для озвучки: без скобок и символов, не длиннее нескольких фраз."""
        if not rows:
            return 'На этот период ничего нет.'
        months = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
                  'августа', 'сентября', 'октября', 'ноября', 'декабря')
        today = self.now_func().astimezone(self.zone).date()
        limit = 6
        days = {}
        for item in rows[:limit]:
            value, at = self._when(item)
            days.setdefault(value[:10] if value else None, []).append((item, at))

        def day_name(day):
            if day is None:
                return 'Без срока'
            d = date.fromisoformat(day)
            named = {0: 'Сегодня', 1: 'Завтра', 2: 'Послезавтра'}.get((d - today).days)
            return named or f'{d.day} {months[d.month - 1]}'

        phrases = []
        for day, items in days.items():
            parts = []
            for item, at in items:
                name = self._title_with_kind(item, spoken=True)
                parts.append(f'в {at:%H:%M} {name}' if at else name)
            phrases.append(f'{day_name(day)}: ' + '; '.join(parts) + '.')

        head = f'{_records(len(rows)).capitalize()}'
        if window:
            head += f' {window}'
        speech = head + '. ' + ' '.join(phrases)
        if len(rows) > limit:
            speech += f' И ещё {_records(len(rows) - limit)} — смотри на экране.'
        return speech

    def _describe_list(self, rows, window=None):
        if not rows:
            if window:
                return f'На выбранный период {window} записей нет.'
            return 'На выбранный период записей нет.'

        grouped = {}

        for item in rows[:100]:
            value = (
                item.get('occurrence_at')
                or item.get('start_at')
                or item.get('due_at')
            )

            day = value[:10] if value else None
            grouped.setdefault(day, []).append(item)

        lines = [f'В расписании {_records(len(rows))}'
                 + (f' {window}' if window else '') + ':']

        for day, items in grouped.items():
            if day:
                heading = date.fromisoformat(day).strftime('%d.%m.%Y')
            else:
                heading = 'Без даты'

            lines.append(f'\n{heading}')

            for item in items:
                value = (
                    item.get('occurrence_at')
                    or item.get('start_at')
                    or item.get('due_at')
                )

                clock = (
                    datetime.fromisoformat(value).strftime('%H:%M')
                    if value and 'T' in value
                    else 'время не указано'
                )

                lines.append(
                    f"• {clock} — {self._title_with_kind(item)}"
                )

        if len(rows) > 100:
            lines.append(f'\nИ ещё {_records(len(rows) - 100)}.')

        return '\n'.join(lines)    
