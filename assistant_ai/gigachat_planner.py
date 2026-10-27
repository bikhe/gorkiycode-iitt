"""LLM is a parser/planner, NEVER a direct database writer."""
from __future__ import annotations
import json

FUNCTION={
    'name':'plan_action',
    'description':'Разбери запрос о задачах, встречах и напоминаниях; верни структурированный план, НЕ выполняй действие.',
    'parameters':{'type':'object','properties':{
        'action':{'type':'string','enum':['create','list','search','update','delete','answer','unknown']},
        'kind':{'type':'string','enum':['task','meeting','reminder','any']},
        'title':{'type':'string'},
        'temporal_text':{'type':'string'},
        'reference':{'type':'string'},
        'query':{'type':'string'},
        'new_title':{'type':'string'},
        'status':{'type':'string','enum':['pending','done']},
        'duration_minutes':{'type':'integer'},
        'recurrence':{'type':'object','properties':{
            'freq':{'type':'string','enum':['daily','weekly','monthly','yearly']},
            'interval':{'type':'integer'},
            'by_weekday':{'type':'array','items':{'type':'integer'}},
            'bymonthday':{'type':'integer'},
            'until':{'type':'string'},
            'count':{'type':'integer'}
        }},
        'negated':{'type':'boolean'},
    },'required':['action']}}

def system_prompt(context,now,catalog):
    """Инструкция планировщику; общая для всех LLM-провайдеров."""
    return (
            'Ты русскоязычный аналитический модуль ассистента задач, встреч и напоминаний. '
            'Вызывай ТОЛЬКО plan_action. Ты не выполняешь CRUD и не подтверждаешь сохранение. '
            'Разбирай свободную речь и разговорные синонимы (имейл/email/Gmail/почта), '
            'но не выдумывай личные факты. Не подменяй исходные даты: в temporal_text дословно '
            'копируй выражение пользователя о времени. '
            '«Напомни» -> kind=reminder; «встреча/созвон» -> meeting; «задача» -> task. '
            'Из названия исключай слова о времени, но сохраняй смысл. '
            'Разделяй просмотр (list) и смысловой поиск (search). '
            'При «не создавай» action=answer, negated=true. '
            '«Перенеси её» ссылается на обсуждаемую запись; не выбирай ID наугад. '
            'Для еженедельных событий by_weekday = [0] для понедельника ... [6] для воскресенья. '
            'Если невозможно определить действие, верни unknown. '
            f'Текущее время: {now.isoformat()} (локальное). '
            f'Контекст: {json.dumps(context,ensure_ascii=False,default=str)[:4500]}. '
            f'Список записей: {json.dumps(catalog,ensure_ascii=False,default=str)[:12000]}.'
        )


class GigaChatPlanner:
    def __init__(self,client):self.client=client

    def plan(self,text,context,now,catalog):
        from gigachat.models import ChatCompletionRequest, ChatMessage, ChatFunctionSpecification, ChatTool
        sys=system_prompt(context,now,catalog)
        request=ChatCompletionRequest(
            messages=[ChatMessage(role='system',content=sys),ChatMessage(role='user',content=text)],
            tools=[ChatTool(functions={'specifications':[ChatFunctionSpecification(**FUNCTION)]})],
        )
        result=self.client.chat.create(request)
        for message in result.messages or []:
            calls=[getattr(message,'function_call',None)]
            calls.extend(getattr(part,'function_call',None) for part in (message.content or []))
            for call in calls:
                if call is not None and call.name=='plan_action':
                    payload=call.arguments
                    if isinstance(payload,str):payload=json.loads(payload)
                    if isinstance(payload,dict):return payload
        return {'action':'unknown'}
