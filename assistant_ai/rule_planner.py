"""OFFLINE diagnostic planner, not a replacement for GigaChat.
Useful for running demo/tests without credentials. Only basic intents.
"""
from __future__ import annotations
import re
from .dates import norm
from .service import _kind_from_text,_fallback_title

class RulePlanner:
    def plan(self,text,context,now,catalog):
        s=norm(text)
        if re.search(r'\bне\s+(?:создавай|добавляй|записывай|удаляй)\b',s):return {'action':'answer','negated':True}
        if re.search(r'\b(?:удали|убери|отмени\s+встречу|сотри)\b',s):return {'action':'delete','reference':text}
        if re.search(r'\b(?:перенеси|передвинь|сдвинь|измени|переименуй|отметь)\b',s):
            status='done' if re.search(r'\b(?:выполненн\w*|сделан\w*|готов\w*)\b',s) else None
            return {'action':'update','reference':'её' if 'ее' in s or 'её' in s else text,'temporal_text':text,'status':status}
        if re.search(r'\b(?:найди|поищи|отыщи|разыщи|когда\s+у\s+меня\s+был)\b',s):
            q=re.sub(r'^.*?\b(?:найди|поищи|отыщи|разыщи)\s*','',s)
            return {'action':'search','query':q,'kind':_kind_from_text(text) or 'any'}
        if re.search(r'\b(?:что\s+у\s+меня|какие\s+планы|что\s+запланирован|есть\s+ли\s+у\s+меня|покажи\s+мои|список\s+задач|какие\s+задачи)\b',s):
            return {'action':'list','temporal_text':text,'kind':_kind_from_text(text) or 'any'}
        if re.search(r'\b(?:напомни|создай|поставь|назначь|добавь|запиши|нужно|не\s+забыть)\b',s) or ('у меня встреча' in s):
            kind=_kind_from_text(text) or 'task'
            title=_fallback_title(text,kind)
            return {'action':'create','kind':kind,'title':title,'temporal_text':text}
        return {'action':'unknown'}
