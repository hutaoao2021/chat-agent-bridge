"""Durable state. All decisions use SQLite write transactions."""
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import json
import secrets
import sqlite3
import time

@dataclass(frozen=True)
class Task:
    id: str
    conversation_id: str
    goal: str
    status: str
    version: int
    turn_count: int
    idempotency_key: str
    checkpoint: str
    def as_dict(self): return asdict(self)

@dataclass(frozen=True)
class Approval:
    id: str
    task_id: str
    action_digest: str
    summary: str
    decision: str
    consumed_at: float | None
    def as_dict(self): return asdict(self)

@dataclass(frozen=True)
class Lease:
    id: str
    task_id: str
    expected_turn: int
    tab_id: str
    expires_at: float
    fingerprint: str | None
    def as_dict(self): return asdict(self)

def digest(value): return hashlib.sha256(value.encode()).hexdigest()

class TaskStore:
    def __init__(self, db_path: Path):
        self.path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
              goal TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
              turn_count INTEGER NOT NULL DEFAULT 0, idempotency_key TEXT NOT NULL,
              checkpoint TEXT NOT NULL DEFAULT '', UNIQUE(conversation_id,idempotency_key));
            CREATE TABLE IF NOT EXISTS bindings(code_hash TEXT PRIMARY KEY, conversation_id TEXT,
              tab_id TEXT, expires_at REAL, consumed_at REAL, task_id TEXT);
            CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, task_id TEXT, action_digest TEXT,
              summary TEXT, decision TEXT DEFAULT 'pending', consumed_at REAL);
            CREATE TABLE IF NOT EXISTS leases(id TEXT PRIMARY KEY, task_id TEXT, expected_turn INTEGER,
              tab_id TEXT, expires_at REAL, fingerprint TEXT, UNIQUE(task_id,expected_turn));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, at REAL, detail TEXT);
            CREATE TABLE IF NOT EXISTS records(namespace TEXT, key TEXT, value TEXT,
              PRIMARY KEY(namespace,key));
            ''')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('BEGIN IMMEDIATE')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally: db.close()

    def event(self, db, task_id, kind, detail=''):
        db.execute('INSERT INTO events(task_id,kind,at,detail) VALUES(?,?,?,?)', (task_id,kind,time.time(),detail))

    def _epoch(self,db,namespace,task_id):
        row=db.execute('SELECT value FROM records WHERE namespace=? AND key=?',(namespace,task_id)).fetchone()
        return json.loads(row['value'])['epoch'] if row else 0

    def _bump_eligibility(self,db,task_id):
        value={'epoch':self._epoch(db,'eligibility',task_id)+1}
        db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',('eligibility',task_id,json.dumps(value)))

    def _ready(self,db,t):
        return (t.status in {'active','waiting_for_model'} and
            self._epoch(db,'eligibility',t.id)>self._epoch(db,'claimed_epoch',t.id) and
            not db.execute('SELECT 1 FROM leases WHERE task_id=? AND expected_turn=?',(t.id,t.turn_count)).fetchone())

    def continuation_ready(self,task_id):
        with self.transaction() as db:return self._ready(db,self._get(db,task_id))

    def validate_send(self,lease_id,task_id,conversation_id,tab_id):
        with self.transaction() as db:
            t=self._get(db,task_id)
            row=db.execute('SELECT * FROM leases WHERE id=?',(lease_id,)).fetchone()
            return bool(row and row['task_id']==task_id and row['tab_id']==str(tab_id) and
                row['fingerprint'] is None and row['expires_at']>time.time() and
                row['expected_turn']==t.turn_count and t.conversation_id==conversation_id and
                t.status in {'active','waiting_for_model'})

    def register_chat_binding(self, conversation_id, tab_id, ttl_seconds=300):
        if not conversation_id or len(conversation_id)>200: raise ValueError('invalid conversation')
        code = secrets.token_urlsafe(24)
        with self.transaction() as db:
            db.execute('INSERT INTO bindings VALUES(?,?,?,?,NULL,NULL)', (digest(code),conversation_id,str(tab_id),time.time()+ttl_seconds))
        return code

    def begin(self, binding_code, goal, idempotency_key):
        if not goal or not idempotency_key: raise ValueError('goal and key required')
        with self.transaction() as db:
            b = db.execute('SELECT * FROM bindings WHERE code_hash=?',(digest(binding_code),)).fetchone()
            if not b: raise ValueError('invalid binding')
            if b['consumed_at']:
                t = self._get(db,b['task_id'])
                if t.idempotency_key == idempotency_key and t.goal == goal: return t
                raise ValueError('binding already consumed')
            if b['expires_at'] < time.time(): raise ValueError('expired binding')
            if db.execute("SELECT 1 FROM tasks WHERE conversation_id=? AND status NOT IN ('completed','blocked','cancelled')",(b['conversation_id'],)).fetchone():
                raise ValueError('conversation already has active task')
            task_id = secrets.token_hex(16)
            db.execute('INSERT INTO tasks(id,conversation_id,goal,status,idempotency_key) VALUES(?,?,?,?,?)', (task_id,b['conversation_id'],goal,'active',idempotency_key))
            db.execute('UPDATE bindings SET consumed_at=?,task_id=? WHERE code_hash=?',(time.time(),task_id,digest(binding_code)))
            self.event(db,task_id,'begin')
            return self._get(db,task_id)

    def _get(self, db, task_id):
        row = db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        if not row: raise ValueError('unknown task')
        return Task(**dict(row))

    def get(self, task_id):
        with self.transaction() as db: return self._get(db,task_id)

    def get_active_for_conversation(self, conversation_id):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM tasks WHERE conversation_id=? AND status NOT IN ('completed','blocked','cancelled') ORDER BY rowid DESC LIMIT 1",(conversation_id,)).fetchone()
            return Task(**dict(row)) if row else None

    def checkpoint(self, task_id, expected_version, text, status='active'):
        if status not in {'active','waiting_for_job','waiting_for_model'}: raise ValueError('invalid checkpoint status')
        with self.transaction() as db:
            t = self._get(db,task_id)
            if t.status in {'completed','blocked','cancelled','waiting_for_approval'}: raise ValueError('task cannot checkpoint now')
            if not db.execute('UPDATE tasks SET checkpoint=?,status=?,version=version+1 WHERE id=? AND version=?',(text,status,task_id,expected_version)).rowcount: raise ValueError('stale version')
            self.event(db,task_id,'checkpoint')
            self._bump_eligibility(db,task_id)
            return self._get(db,task_id)

    def finish(self, task_id, expected_version, status):
        if status not in {'completed','blocked','cancelled'}: raise ValueError('invalid terminal status')
        with self.transaction() as db:
            if not db.execute('UPDATE tasks SET status=?,version=version+1 WHERE id=? AND version=?',(status,task_id,expected_version)).rowcount: raise ValueError('stale version')
            self.event(db,task_id,status)
            return self._get(db,task_id)

    def request_approval(self, task_id, action_digest, summary):
        with self.transaction() as db:
            t = self._get(db,task_id)
            if t.status in {'completed','blocked','cancelled'}: raise ValueError('task ended')
            row = db.execute("SELECT * FROM approvals WHERE task_id=? AND action_digest=? AND decision='pending'",(task_id,action_digest)).fetchone()
            if row: return Approval(**dict(row))
            aid = secrets.token_hex(16)
            db.execute('INSERT INTO approvals(id,task_id,action_digest,summary) VALUES(?,?,?,?)',(aid,task_id,action_digest,summary))
            db.execute("UPDATE tasks SET status='waiting_for_approval',version=version+1 WHERE id=?",(task_id,))
            self.event(db,task_id,'approval_requested',aid)
            return Approval(**dict(db.execute('SELECT * FROM approvals WHERE id=?',(aid,)).fetchone()))

    def decide_approval(self, approval_id, approved):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM approvals WHERE id=?',(approval_id,)).fetchone()
            if not row or row['decision']!='pending': raise ValueError('approval unavailable')
            decision = 'approved' if approved else 'rejected'
            db.execute('UPDATE approvals SET decision=? WHERE id=?',(decision,approval_id))
            if not db.execute("SELECT 1 FROM approvals WHERE task_id=? AND decision='pending'",(row['task_id'],)).fetchone():
                db.execute("UPDATE tasks SET status='waiting_for_model',version=version+1 WHERE id=? AND status='waiting_for_approval'",(row['task_id'],))
                self._bump_eligibility(db,row['task_id'])
            self.event(db,row['task_id'],'approval_'+decision,approval_id)
            return Approval(**dict(db.execute('SELECT * FROM approvals WHERE id=?',(approval_id,)).fetchone()))

    def consume_approval(self, approval_id, action_digest):
        with self.transaction() as db:
            return bool(db.execute("UPDATE approvals SET consumed_at=? WHERE id=? AND action_digest=? AND decision='approved' AND consumed_at IS NULL",(time.time(),approval_id,action_digest)).rowcount)

    def approvals(self, task_id):
        with self.transaction() as db:
            return [dict(r) for r in db.execute("SELECT * FROM approvals WHERE task_id=? AND consumed_at IS NULL",(task_id,))]

    def claim_continuation(self, task_id, conversation_id, tab_id, expected_turn, ttl_seconds=30):
        with self.transaction() as db:
            t = self._get(db,task_id)
            if t.conversation_id!=conversation_id or t.turn_count!=expected_turn or not self._ready(db,t): return None
            lid = secrets.token_hex(16)
            try:
                db.execute('INSERT INTO leases VALUES(?,?,?,?,?,NULL)',(lid,task_id,expected_turn,str(tab_id),time.time()+ttl_seconds))
            except sqlite3.IntegrityError: return None
            self.event(db,task_id,'lease',lid)
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',('claimed_epoch',task_id,json.dumps({'epoch':self._epoch(db,'eligibility',task_id)})))
            return Lease(**dict(db.execute('SELECT * FROM leases WHERE id=?',(lid,)).fetchone()))

    def reconcile_continuation(self,task_id,conversation_id):
        """Explicit human reconciliation retires uncertain send; never retries it."""
        with self.transaction() as db:
            task=self._get(db,task_id)
            if task.conversation_id!=conversation_id or task.status in {'completed','blocked','cancelled'}:raise ValueError('task cannot reconcile')
            lease=db.execute('SELECT * FROM leases WHERE task_id=? AND expected_turn=?',(task_id,task.turn_count)).fetchone()
            if lease:
                db.execute("UPDATE leases SET fingerprint='human:abandoned' WHERE id=?",(lease['id'],))
                db.execute('UPDATE tasks SET turn_count=turn_count+1,version=version+1 WHERE id=?',(task_id,))
            self._bump_eligibility(db,task_id)
            self.event(db,task_id,'human_reconciled')
            return self._get(db,task_id)

    def ack_continuation(self, lease_id, message_fingerprint):
        if not message_fingerprint: raise ValueError('fingerprint required')
        with self.transaction() as db:
            r = db.execute('SELECT * FROM leases WHERE id=?',(lease_id,)).fetchone()
            if not r: raise ValueError('unknown lease')
            if r['fingerprint']:
                if r['fingerprint']!=message_fingerprint: raise ValueError('conflicting acknowledgement')
                return
            if r['expires_at']<time.time(): raise ValueError('expired lease; pause required')
            db.execute('UPDATE leases SET fingerprint=? WHERE id=?',(message_fingerprint,lease_id))
            db.execute('UPDATE tasks SET turn_count=turn_count+1,version=version+1 WHERE id=?',(r['task_id'],))
            self.event(db,r['task_id'],'ack',lease_id)

    def record(self, namespace, key):
        with self.transaction() as db:
            r = db.execute('SELECT value FROM records WHERE namespace=? AND key=?',(namespace,key)).fetchone()
            return json.loads(r['value']) if r else None

    def reserve_record(self, namespace, key, value):
        with self.transaction() as db:
            return bool(db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?)',(namespace,key,json.dumps(value))).rowcount)

    def put_record(self, namespace, key, value):
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(namespace,key,json.dumps(value)))

    def records(self, namespace):
        with self.transaction() as db:
            return [json.loads(r['value']) for r in db.execute('SELECT value FROM records WHERE namespace=?',(namespace,))]
