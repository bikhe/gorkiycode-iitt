"""Persistent SQLite implementation of CrudPort, suitable for a single-node demo.

Includes per-user isolation, session persistence, soft delete, JSON recurrence,
write locks, request-id idempotency, optional semantic search (embeddings)
and storage for web accounts. Password hashing and HTTP live in auth.py.
"""
from __future__ import annotations
import json
import logging
import math
import sqlite3
import threading
from array import array
from datetime import datetime, timedelta, timezone
from .search_helpers import rank

log=logging.getLogger(__name__)

COLUMNS={'type','title','start_at','due_at','duration_minutes','recurrence','status'}
TYPES=('task','meeting','reminder')
STATUSES=('pending','done')
KIND_LABEL={'task':'Задача','meeting':'Встреча','reminder':'Напоминание'}

def _utcnow():
    return datetime.now(timezone.utc)

def _cosine(a,b):
    dot=sum(x*y for x,y in zip(a,b))
    na=math.sqrt(sum(x*x for x in a));nb=math.sqrt(sum(y*y for y in b))
    return dot/(na*nb) if na and nb else 0.0

class SQLiteRepository:
    def __init__(self,path='assistant_data.sqlite3',embedder=None,semantic_threshold=None,semantic_margin=0.08):
        """embedder: см. embeddings.py, None — только поиск по словам.

        semantic_threshold: минимальное сходство (по умолчанию — порог модели);
        semantic_margin: насколько хуже лучшего совпадения ещё можно показать.
        """
        self._lock=threading.RLock()
        self.embedder=embedder
        self.semantic_threshold=(semantic_threshold if semantic_threshold is not None
                                 else getattr(embedder,'threshold',0.6))
        self.semantic_margin=semantic_margin
        self.connection=sqlite3.connect(str(path),check_same_thread=False,timeout=10)
        self.connection.row_factory=sqlite3.Row
        self.connection.execute('PRAGMA busy_timeout=10000')
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA foreign_keys=ON')
        with self._lock,self.connection:
            self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id TEXT NOT NULL,
              request_id TEXT NOT NULL,
              type TEXT NOT NULL,
              title TEXT NOT NULL,
              start_at TEXT,
              due_at TEXT,
              duration_minutes INTEGER NOT NULL DEFAULT 60,
              recurrence TEXT,
              status TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL,
              deleted INTEGER NOT NULL DEFAULT 0,
              UNIQUE(user_id, request_id)
            );
            CREATE INDEX IF NOT EXISTS records_user ON records(user_id,deleted);
            CREATE TABLE IF NOT EXISTS exchanges (
              user_id TEXT NOT NULL,
              session_id TEXT NOT NULL,
              request_id TEXT NOT NULL,
              result TEXT NOT NULL,
              PRIMARY KEY (user_id,session_id,request_id)
            );
            CREATE TABLE IF NOT EXISTS sessions (
              user_id TEXT NOT NULL,
              session_id TEXT NOT NULL,
              state TEXT NOT NULL,
              PRIMARY KEY(user_id,session_id)
            );
            CREATE TABLE IF NOT EXISTS embeddings (
              record_id INTEGER PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
              text TEXT NOT NULL,
              vector BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE COLLATE NOCASE,
              password_hash TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
              token_hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at TEXT NOT NULL
            );
            ''')
            # Миграция баз, созданных до появления очистки по времени.
            for table in ('exchanges','sessions'):
                columns={r[1] for r in self.connection.execute(f'PRAGMA table_info({table})')}
                if 'updated_at' not in columns:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
            # Векторы разных моделей несравнимы: помним, какой моделью посчитан.
            if 'model' not in {r[1] for r in self.connection.execute('PRAGMA table_info(embeddings)')}:
                self.connection.execute("ALTER TABLE embeddings ADD COLUMN model TEXT NOT NULL DEFAULT ''")
        self.cleanup()

    @staticmethod
    def _as_item(row):
        if row is None:return None
        o=dict(row)
        o['recurrence']=json.loads(o['recurrence']) if o.get('recurrence') else None
        o['deleted']=bool(o['deleted'])
        return o

    def get_exchange(self,user_id,session_id,request_id):
        with self._lock:
            row=self.connection.execute('SELECT result FROM exchanges WHERE user_id=? AND session_id=? AND request_id=?',(user_id,session_id,request_id)).fetchone()
        return json.loads(row[0]) if row else None

    def save_exchange(self,user_id,session_id,request_id,result):
        with self._lock,self.connection:
            self.connection.execute('INSERT OR IGNORE INTO exchanges(user_id,session_id,request_id,result,updated_at) VALUES(?,?,?,?,?)',
                                    (user_id,session_id,request_id,json.dumps(result,ensure_ascii=False,default=str),_utcnow().isoformat()))

    def cleanup(self,exchange_days=7,session_days=30):
        """Удаляет старые ответы идемпотентности, диалоги и истёкшие входы."""
        now=_utcnow()
        with self._lock,self.connection:
            self.connection.execute('DELETE FROM exchanges WHERE updated_at < ?',((now-timedelta(days=exchange_days)).isoformat(),))
            self.connection.execute('DELETE FROM sessions WHERE updated_at < ?',((now-timedelta(days=session_days)).isoformat(),))
            self.connection.execute('DELETE FROM auth_sessions WHERE expires_at < ?',(now.isoformat(),))

    def close(self):
        with self._lock:self.connection.close()

    def get_session(self,user_id,session_id):
        with self._lock:
            row=self.connection.execute('SELECT state FROM sessions WHERE user_id=? AND session_id=?',(user_id,session_id)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_session(self,user_id,session_id,state):
        with self._lock,self.connection:
            self.connection.execute('''INSERT INTO sessions(user_id,session_id,state,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(user_id,session_id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at''',
                (user_id,session_id,json.dumps(state,ensure_ascii=False,default=str),_utcnow().isoformat()))

    def create_item(self,user_id,data,request_id):
        if not request_id or not user_id: raise ValueError('Требуется ID операции и пользователя')
        if data.get('type') not in TYPES or not str(data.get('title') or '').strip():
            raise ValueError('Некорректная запись.')
        with self._lock,self.connection:
            self.connection.execute('''INSERT OR IGNORE INTO records
                (user_id,request_id,type,title,start_at,due_at,duration_minutes,recurrence,status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',(
                user_id,request_id,data['type'],str(data['title']).strip()[:250],data.get('start_at'),data.get('due_at'),
                int(data.get('duration_minutes') or 60),json.dumps(data['recurrence'],ensure_ascii=False) if data.get('recurrence') else None,
                data.get('status') or 'pending',_utcnow().isoformat()
            ))
            row=self.connection.execute('SELECT * FROM records WHERE user_id=? AND request_id=?',(user_id,request_id)).fetchone()
        item=self._as_item(row)
        self._embed_items([item])
        return item

    def get_item(self,user_id,item_id):
        if item_id is None:return None
        with self._lock:
            row=self.connection.execute('SELECT * FROM records WHERE user_id=? AND id=? AND deleted=0',(user_id,item_id)).fetchone()
        return self._as_item(row)

    def _all(self,user_id):
        with self._lock:
            rows=self.connection.execute('SELECT * FROM records WHERE user_id=? AND deleted=0 ORDER BY id',(user_id,)).fetchall()
        return [self._as_item(row) for row in rows]

    def list_items(self,user_id,date_from,date_to,kind=None):
        return [row for row in self._all(user_id)
                if (kind is None or row['type']==kind)
                and (not(row.get('start_at') or row.get('due_at')) or row.get('recurrence') or
                     date_from <= (row.get('start_at') or row.get('due_at'))[:10] <= date_to)]

    # --- поиск ---------------------------------------------------------

    @staticmethod
    def _embed_text(item):
        return f"{KIND_LABEL.get(item['type'],'Запись')}: {item['title']}"

    def _embed_items(self,items):
        """Считает и сохраняет векторы. Ошибка эмбеддингов не ломает запись."""
        if not self.embedder or not items:return
        texts=[self._embed_text(x) for x in items]
        try:
            vectors=self.embedder(texts)
        except Exception as exc:
            log.warning('Не удалось посчитать эмбеддинги: %s',exc)
            return
        with self._lock,self.connection:
            self.connection.executemany('INSERT OR REPLACE INTO embeddings(record_id,text,model,vector) VALUES(?,?,?,?)',
                [(x['id'],t,self._model_name,array('f',v).tobytes()) for x,t,v in zip(items,texts,vectors)])

    @property
    def _model_name(self):
        return getattr(self.embedder,'name','')

    def _load_vectors(self,ids):
        with self._lock:
            rows=self.connection.execute('SELECT record_id,text,model,vector FROM embeddings WHERE record_id IN (%s)'%','.join('?'*len(ids)),ids).fetchall()
        return {r[0]:(r[1],r[2],r[3]) for r in rows}

    def _vectors(self,items):
        """record_id -> вектор текущей модели; недостающие и устаревшие
        (старые записи, сбой модели, переименование, смена модели) досчитываются."""
        ids=[x['id'] for x in items]
        known=self._load_vectors(ids)
        stale=[x for x in items if known.get(x['id'],(None,None))[:2]!=(self._embed_text(x),self._model_name)]
        for i in range(0,len(stale),50):
            self._embed_items(stale[i:i+50])
        if stale:known=self._load_vectors(ids)
        out={}
        for record_id,(_,model,blob) in known.items():
            if model!=self._model_name:continue
            vector=array('f');vector.frombytes(blob);out[record_id]=vector
        return out

    def search_items(self,user_id,query,limit=30):
        """Сначала совпадения по словам, затем по смыслу.

        Смысловое совпадение показывается, если сходство не ниже порога и
        не сильно хуже лучшего: абсолютный порог один не отделяет верное
        от похожего («презентация» близка и к «отчёту»).
        """
        items=self._all(user_id)
        lexical=rank(query,items,limit)
        if not self.embedder or not items:return lexical
        try:
            q=self.embedder([query])[0]
        except Exception as exc:
            log.warning('Семантический поиск недоступен, остаётся лексический: %s',exc)
            return lexical
        vectors=self._vectors(items)
        scored=sorted(((_cosine(q,vectors[x['id']]),x) for x in items if x['id'] in vectors),key=lambda pair:-pair[0])
        if not scored:return lexical
        floor=max(self.semantic_threshold,scored[0][0]-self.semantic_margin)
        found={x['id'] for x in lexical}
        return (lexical+[x for value,x in scored if value>=floor and x['id'] not in found])[:limit]

    # --- изменения -----------------------------------------------------

    def update_item(self,user_id,item_id,changes):
        unknown=set(changes)-COLUMNS
        if unknown:raise ValueError('Недопустимые поля изменения.')
        if not changes:raise ValueError('Нет изменений')
        if 'type' in changes and changes['type'] not in TYPES:raise ValueError('Некорректный тип записи.')
        if 'status' in changes and changes['status'] not in STATUSES:raise ValueError('Некорректный статус.')
        if 'title' in changes:
            title=str(changes['title'] or '').strip()
            if not title:raise ValueError('Пустое название.')
            changes={**changes,'title':title[:250]}
        if 'duration_minutes' in changes:
            minutes=60 if changes['duration_minutes'] is None else int(changes['duration_minutes'])
            if not 1<=minutes<=24*60:raise ValueError('Длительность должна быть от 1 минуты до суток.')
            changes={**changes,'duration_minutes':minutes}
        payload={k:(json.dumps(v,ensure_ascii=False) if k=='recurrence' and v else v) for k,v in changes.items()}
        with self._lock,self.connection:
            cur=self.connection.execute('UPDATE records SET '+', '.join(k+'=?' for k in payload)+' WHERE id=? AND user_id=? AND deleted=0',
                                        (*payload.values(),item_id,user_id))
            if cur.rowcount!=1:raise ValueError('Запись не найдена')
        item=self.get_item(user_id,item_id)
        if {'title','type'}&set(changes):self._embed_items([item])
        return item

    def delete_item(self,user_id,item_id):
        with self._lock,self.connection:
            cur=self.connection.execute('UPDATE records SET deleted=1 WHERE id=? AND user_id=? AND deleted=0',(item_id,user_id))
        return cur.rowcount==1

    # --- учётные записи (веб-вход) --------------------------------------

    def create_user(self,username,password_hash):
        """Возвращает id нового пользователя или None, если имя занято."""
        try:
            with self._lock,self.connection:
                cur=self.connection.execute('INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)',
                                            (username,password_hash,_utcnow().isoformat()))
        except sqlite3.IntegrityError:
            return None
        return cur.lastrowid

    def get_user_by_name(self,username):
        with self._lock:
            row=self.connection.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        return dict(row) if row else None

    def create_auth_session(self,token_hash,user_id,expires_at):
        with self._lock,self.connection:
            self.connection.execute('INSERT INTO auth_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
                                    (token_hash,user_id,expires_at.isoformat()))

    def get_auth_user(self,token_hash):
        """Пользователь по хешу токена, если вход ещё не истёк."""
        with self._lock:
            row=self.connection.execute('''SELECT users.id,users.username FROM auth_sessions JOIN users ON users.id=auth_sessions.user_id
                WHERE token_hash=? AND expires_at>?''',(token_hash,_utcnow().isoformat())).fetchone()
        return dict(row) if row else None

    def delete_auth_session(self,token_hash):
        with self._lock,self.connection:
            self.connection.execute('DELETE FROM auth_sessions WHERE token_hash=?',(token_hash,))
