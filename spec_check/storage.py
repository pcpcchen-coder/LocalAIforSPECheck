"""Local SQLite persistence. Review changes and their audit events are atomic."""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid4().hex


class ConflictError(ValueError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        (self.root / 'uploads').mkdir(exist_ok=True)
        self.path = self.root / 'spec_check.sqlite3'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, parent TEXT,
                    data TEXT NOT NULL, PRIMARY KEY(kind,id));
                CREATE INDEX IF NOT EXISTS records_parent ON records(kind,parent);
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY, result_id TEXT NOT NULL,
                    version INTEGER NOT NULL, data TEXT NOT NULL,
                    UNIQUE(result_id,version));
            ''')
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def put(self, kind, obj, parent=None):
        self.put_many([(kind, obj, parent)])
        return obj

    def put_many(self, items):
        """Commit related records together (for example a result and its count)."""
        with self.connect() as db:
            db.executemany('INSERT INTO records(kind,id,parent,data) VALUES(?,?,?,?) '
                           'ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data,parent=excluded.parent',
                           [(kind, obj['id'], parent, json.dumps(obj, ensure_ascii=False))
                            for kind, obj, parent in items])

    def get(self, kind, identifier):
        with self.connect() as db:
            row = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind,identifier)).fetchone()
        if row is None:
            raise KeyError(identifier)
        return json.loads(row['data'])

    def list(self, kind, parent=None):
        with self.connect() as db:
            if parent is None:
                rows = db.execute('SELECT data FROM records WHERE kind=? ORDER BY rowid', (kind,)).fetchall()
            else:
                rows = db.execute('SELECT data FROM records WHERE kind=? AND parent=? ORDER BY rowid', (kind,parent)).fetchall()
        return [json.loads(r['data']) for r in rows]

    def count(self, kind, parent):
        with self.connect() as db:
            return db.execute('SELECT COUNT(*) FROM records WHERE kind=? AND parent=?',
                              (kind,parent)).fetchone()[0]

    def history(self, result_id):
        with self.connect() as db:
            return [json.loads(r['data']) for r in db.execute(
                'SELECT data FROM reviews WHERE result_id=? ORDER BY version', (result_id,))]

    def run_snapshot(self, run_id):
        """Read verdicts and audit events from one consistent SQLite snapshot."""
        with self.connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT data FROM records WHERE kind=? AND id=?', ('run',run_id)).fetchone()
            if row is None:
                raise KeyError(run_id)
            run = json.loads(row['data'])
            progress = db.execute('SELECT data FROM records WHERE kind=? AND id=?',
                                  ('run_progress', run_id)).fetchone()
            if progress is not None:
                run.update(json.loads(progress['data']))
            results = [json.loads(r['data']) for r in db.execute(
                'SELECT data FROM records WHERE kind=? AND parent=? ORDER BY rowid', ('result',run_id))]
            for result in results:
                result['history'] = [json.loads(r['data']) for r in db.execute(
                    'SELECT data FROM reviews WHERE result_id=? ORDER BY version', (result['id'],))]
            run['results'] = results
        return run

    def review(self, result_id, values):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM records WHERE kind=? AND id=?', ('result',result_id)).fetchone()
            if row is None:
                raise KeyError(result_id)
            result = json.loads(row['data'])
            version = result['review']['version']
            if values['expected_version'] != version:
                raise ConflictError('此項目已由另一個視窗更新。請重新讀取後確認。')
            decision = values['decision']
            final_status = result['status'] if decision == 'confirmed' else values.get('final_status')
            if decision == 'reopened':
                final_status = None
            event = dict(id=uid(), result_id=result_id, decision=decision,
                         final_status=final_status, reviewer=values['reviewer'].strip(),
                         note=values.get('note','').strip(), created_at=now(), version=version+1)
            result['review'] = {k:v for k,v in event.items() if k not in ('id','result_id','created_at')}
            result['review']['updated_at'] = event['created_at']
            db.execute('INSERT INTO reviews VALUES(?,?,?,?)',
                       (event['id'],result_id,version+1,json.dumps(event, ensure_ascii=False)))
            db.execute('UPDATE records SET data=? WHERE kind=? AND id=?',
                       (json.dumps(result,ensure_ascii=False),'result',result_id))
        result['history'] = self.history(result_id)
        return result
