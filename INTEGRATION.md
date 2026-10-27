# Интеграция: голос, Django, данные, безопасность

## API AI как обычной Python-библиотеки

**Единственная важная функция:**

```python
result = assistant.handle_message(text, user_id='u-42', session_id='session-1')
```

Входы: `text: str` (распознанная или набранная фраза), `user_id` (серверный идентификатор авторизованного пользователя), `session_id` (устойчивый идентификатор разговора).

Ответ: JSON-подобный dict с полями `status`, `action`, `display_text`, `speech_text`, `item`; иногда `items`, `conflicts`.

**Диалоговое правило:** каждый ответ на уточнение отправлять в ту же сессию. Не менять `session_id` после каждого голосового фрагмента.

## Django view: безопасный принцип подключения

Ниже именно *схема подключения*, не готовый Django-проект. Базу `CrudPort` нужно сопоставить с моделями бэкенда. `AssistantAI` и `GigaChatPlanner` не требуют Django и не должны непосредственно знать его ORM.

```python
import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required

# assistant = AssistantAI(GigaChatPlanner(client), django_crud_adapter)

@login_required
@require_POST
def assistant_message(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'invalid_json'}, status=400)
    text = data.get('text')
    if not isinstance(text, str) or not text.strip():
        return JsonResponse({'error': 'text_required'}, status=400)
    session_id = str(data.get('session_id') or request.session.session_key or 'default')[:100]
    # ВАЖНО: user_id никогда не берём из HTTP body!
    result = assistant.handle_message(text, user_id=str(request.user.id), session_id=session_id)
    return JsonResponse(result, json_dumps_params={'ensure_ascii': False})
```

В Django сохраняйте стандартную CSRF-защиту и аутентификацию. Для отдельных worker-процессов нужна серверная БД и транзакционная идемпотентность. Не храните токен GigaChat в JavaScript.

## CrudPort, который нужен для Django

В `assistant_ai/ports.py` определён протокол:

```python
create_item(user_id, data, request_id) -> item
get_item(user_id, item_id) -> item | None
list_items(user_id, date_from, date_to, kind=None) -> list[item]
search_items(user_id, query) -> list[item]
update_item(user_id, item_id, changes) -> item
delete_item(user_id, item_id) -> bool
get_session(user_id, session_id) -> dict
save_session(user_id, session_id, state) -> None
```

Поле записи: `id`, `user_id`, `type` (`task|meeting|reminder`), `title`, `start_at`, `due_at`, `duration_minutes`, `recurrence` (dict/None), `status` (`pending|done`), `created_at`. Все даты в формате `YYYY-MM-DD` или ISO8601 с часовым поясом для времени.

При `list_items` нужно включать **шаблоны повторяющихся событий**, даже когда первая дата шаблона раньше запрошенного периода: AssistantAI разворачивает повторения в экземпляры.

`request_id` у `create_item` — уникальный ключ идемпотентности внутри одного пользователя. Ошибки БД должны приводить к `status=error`, а не к вымышленному «готово».

## Минимальная интеграция с существующей системой

1. Бэкенд реализует CrudPort поверх Django ORM или временно использует предоставленный `SQLiteRepository`.
2. Ассистент принимает итоговый STT-текст, возвращает `speech_text` и `display_text`.
3. Голосовой фронтенд произносит `speech_text`, показывает `display_text` и отправляет следующие ответы с тем же session ID.
4. Обязательно проверяются 4 сценария ТЗ, удаление с подтверждением, тайм-аут GigaChat, конфликт, перезапуск.

## Что не является частью AI-модуля

- Постоянное фоновое воспроизведение напоминаний, Web Push, Telegram-уведомления.
- Настоящая авторизация публичного сайта, HTTPS-развёртывание, Django миграции и модель пользователей.
- Готовый STT/TTS движок: используется Web Speech API/другой механизм команды фронтенда.

## Проверка правильности времени

LLM только планирует действие. `DateResolver` независимо разбирает календарное время из исходной фразы и отклоняет часть недопустимых/неоднозначных выражений. Форматы включают `пол одиннадцатого вечера` → 22:30, `11.30` в контексте времени → 11:30, `на 11.11` в контексте даты → 11 ноября.

Повторения реализованы через `dateutil.rrule`; исключения из серий пока не поддерживаются и не должны имитироваться удалением всей серии.
