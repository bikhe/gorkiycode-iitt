"""Планировщик через любой OpenAI-совместимый Chat Completions API.

Подходит для OpenAI, DeepSeek, OpenRouter, YandexGPT (OpenAI-совместимый
режим), локальной Ollama / LM Studio / vLLM. Нужна поддержка tools
(function calling). Как и GigaChatPlanner, только разбирает запрос.
"""
from __future__ import annotations
import json
import httpx
from .gigachat_planner import FUNCTION, system_prompt

# Готовые адреса; любой другой — через LLM_BASE_URL.
PRESETS={
    'openai':'https://api.openai.com/v1',
    'deepseek':'https://api.deepseek.com/v1',
    'openrouter':'https://openrouter.ai/api/v1',
    'yandex':'https://llm.api.cloud.yandex.net/v1',
    'ollama':'http://localhost:11434/v1',
}

class OpenAICompatiblePlanner:
    def __init__(self,base_url,model,api_key='',timeout=45,extra_headers=None,transport=None):
        if not base_url or not model:
            raise RuntimeError('Для LLM-провайдера нужны LLM_BASE_URL (или пресет) и LLM_MODEL.')
        headers={'Authorization':f'Bearer {api_key}'} if api_key else {}
        headers.update(extra_headers or {})
        self.model=model
        self.http=httpx.Client(base_url=base_url.rstrip('/'),headers=headers,timeout=timeout,transport=transport)

    def close(self):
        self.http.close()

    def plan(self,text,context,now,catalog):
        response=self.http.post('/chat/completions',json={
            'model':self.model,
            'temperature':0,
            'messages':[
                {'role':'system','content':system_prompt(context,now,catalog)},
                {'role':'user','content':text},
            ],
            'tools':[{'type':'function','function':FUNCTION}],
            'tool_choice':{'type':'function','function':{'name':FUNCTION['name']}},
        })
        response.raise_for_status()
        for choice in response.json().get('choices') or []:
            message=choice.get('message') or {}
            calls=message.get('tool_calls') or []
            if message.get('function_call'):calls.append({'function':message['function_call']})  # старый формат
            for call in calls:
                function=call.get('function') or {}
                if function.get('name')==FUNCTION['name']:
                    payload=function.get('arguments')
                    if isinstance(payload,str):payload=json.loads(payload)
                    if isinstance(payload,dict):return payload
        return {'action':'unknown'}
