"""Векторы текста для семантического поиска: локальная модель или GigaChat.

Эмбеддер — вызываемый объект list[str] -> list[list[float]] с атрибутами
name (векторы разных моделей несравнимы) и threshold (порог сходства).
Если эмбеддер не готов или сломан, он бросает EmbeddingsUnavailable,
а репозиторий откатывается на поиск по словам.
"""
from __future__ import annotations
import logging
import threading
import time

log=logging.getLogger(__name__)

# Пороги подобраны на запросах из ТЗ («задачу про презентацию» -> «слайды
# к выступлению», «сходить к зубному» -> «стоматолог»): выше порога — только
# верные совпадения, без ложных на «погода в Москве», «рецепт борща».
LOCAL_MODELS={
    'sentence-transformers/paraphrase-multilingual-mpnet-base-v2':0.6,   # ~1 ГБ, точнее
    'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2':0.55,  # ~0.2 ГБ, для слабого сервера
}
DEFAULT_LOCAL_MODEL='sentence-transformers/paraphrase-multilingual-mpnet-base-v2'


class EmbeddingsUnavailable(RuntimeError):
    pass


class LocalEmbedder:
    """Модель sentence-transformers через fastembed (ONNX, без torch и интернета после загрузки).

    Модель грузится в фоне при старте (первый раз — скачивание ~1 ГБ):
    пока она не готова, поиск работает по словам, а не ждёт.
    """

    def __init__(self,model=DEFAULT_LOCAL_MODEL,cache_dir=None,background=True):
        self.name=f'local:{model}'
        self.threshold=LOCAL_MODELS.get(model,0.6)
        self.model_name,self.cache_dir=model,cache_dir
        self._model=None
        self._error=None
        self._ready=threading.Event()
        self._lock=threading.Lock()
        if background:
            threading.Thread(target=self._load,name='embeddings-load',daemon=True).start()
        else:
            self._load()

    def _load(self):
        try:
            from fastembed import TextEmbedding
            started=time.monotonic()
            self._model=TextEmbedding(self.model_name,cache_dir=self.cache_dir)
            log.info('Модель эмбеддингов %s готова за %.0f с',self.model_name,time.monotonic()-started)
        except Exception as exc:
            self._error=exc
            log.warning('Локальная модель эмбеддингов не загрузилась: %s',exc)
        finally:
            self._ready.set()

    def wait(self,timeout=None):
        return self._ready.wait(timeout)

    def __call__(self,texts:list[str])->list[list[float]]:
        if self._model is None:
            raise EmbeddingsUnavailable('модель ещё загружается' if self._error is None else str(self._error))
        with self._lock:
            return [vector.tolist() for vector in self._model.embed(texts)]


class GigaChatEmbedder:
    """GigaChat Embeddings (нужен тариф с моделью Embeddings).

    После сбоя API пауза, чтобы не ходить в него на каждый запрос:
    402/403 (не входит в тариф, нет доступа) — на час, прочие — на минуту.
    """

    threshold=0.75  # не откалиброван: на нашем тарифе API отвечает 402

    def __init__(self,client,model='Embeddings'):
        self.client,self.model=client,model
        self.name=f'gigachat:{model}'
        self.down_until=0.0

    def __call__(self,texts:list[str])->list[list[float]]:
        if time.monotonic()<self.down_until:
            raise EmbeddingsUnavailable('GigaChat Embeddings временно отключены')
        try:
            result=self.client.embeddings(texts,model=self.model)
        except Exception as exc:
            status=getattr(exc,'status_code',None)
            pause=3600 if status in (402,403) else 60
            self.down_until=time.monotonic()+pause
            log.warning('GigaChat Embeddings недоступны (%s), пауза %s c',status,pause)
            raise EmbeddingsUnavailable(str(status)) from exc
        return [x.embedding for x in sorted(result.data,key=lambda x:x.index)]
