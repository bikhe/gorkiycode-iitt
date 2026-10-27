"""Application bootstrap: used by demo and local HTTP integration."""
from __future__ import annotations
import logging
import os
from pathlib import Path
from .service import AssistantAI
from .rule_planner import RulePlanner
from .gigachat_planner import GigaChatPlanner
from .openai_planner import PRESETS, OpenAICompatiblePlanner
from .sqlite_repository import SQLiteRepository
from .demo_repository import DemoRepository
from .embeddings import DEFAULT_LOCAL_MODEL, GigaChatEmbedder, LocalEmbedder

log=logging.getLogger(__name__)


def make_gigachat_client():
    if not os.getenv('GIGACHAT_CREDENTIALS') and not os.getenv('GIGACHAT_ACCESS_TOKEN'):
        raise RuntimeError('Не найден GIGACHAT_CREDENTIALS. Добавьте .env или запустите --offline.')
    from gigachat import GigaChat
    # .env variable GIGACHAT_CA_BUNDLE_FILE is understood by SDK.
    # Verify the configured file exists BEFORE starting client.
    cert=os.getenv('GIGACHAT_CA_BUNDLE_FILE')
    if cert:
        path=Path(cert)
        if not path.is_absolute():path=Path.cwd()/path
        if not path.is_file():
            raise RuntimeError('Файл сертификата GigaChat не найден: '+str(path))
        os.environ['GIGACHAT_CA_BUNDLE_FILE']=str(path)
    return GigaChat()


def make_planner(offline=False):
    """APP_LLM_PROVIDER: gigachat (по умолчанию) | openai | deepseek | openrouter
    | yandex | ollama | openai_compatible | rules. Возвращает (planner, client);
    client закрывается при остановке сервера, у GigaChat он же нужен эмбеддингам."""
    provider='rules' if offline else os.getenv('APP_LLM_PROVIDER','gigachat').strip().lower()
    if provider=='rules':
        return RulePlanner(),None
    if provider=='gigachat':
        client=make_gigachat_client()
        return GigaChatPlanner(client),client
    if provider in PRESETS or provider=='openai_compatible':
        planner=OpenAICompatiblePlanner(
            os.getenv('LLM_BASE_URL') or PRESETS.get(provider,''),
            os.getenv('LLM_MODEL',''),
            api_key=os.getenv('LLM_API_KEY',''),
            timeout=float(os.getenv('LLM_TIMEOUT','45')),
        )
        return planner,planner
    raise RuntimeError(f'Неизвестный APP_LLM_PROVIDER={provider!r}')


def make_application(*,offline=False,memory=False,database=None):
    from dotenv import load_dotenv
    load_dotenv()
    planner,client=make_planner(offline)
    gigachat=client if isinstance(planner,GigaChatPlanner) else None
    repository=DemoRepository() if memory else SQLiteRepository(
        database or os.getenv('APP_DB_PATH','assistant_data.sqlite3'),
        embedder=make_embedder(gigachat),
        semantic_threshold=float(os.environ['APP_SEMANTIC_THRESHOLD']) if os.getenv('APP_SEMANTIC_THRESHOLD') else None)
    app=AssistantAI(planner,repository,timezone_name=os.getenv('APP_TIMEZONE','Europe/Moscow'))
    return app,client


def make_embedder(client):
    """APP_EMBEDDINGS: local (по умолчанию) | gigachat | off."""
    kind=os.getenv('APP_EMBEDDINGS','local').strip().lower()
    if kind=='off':
        return None
    if kind=='gigachat':
        if client is None:
            log.warning('APP_EMBEDDINGS=gigachat недоступен без GigaChat (офлайн): поиск только по словам')
            return None
        return GigaChatEmbedder(client,os.getenv('GIGACHAT_EMBEDDINGS_MODEL','Embeddings'))
    if kind!='local':
        raise RuntimeError('APP_EMBEDDINGS должен быть local, gigachat или off')
    return LocalEmbedder(os.getenv('APP_EMBEDDINGS_MODEL') or DEFAULT_LOCAL_MODEL,
                         cache_dir=os.getenv('FASTEMBED_CACHE_PATH') or None)
