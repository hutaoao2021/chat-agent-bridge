import re
import secrets
import time
from .state import digest

class PairingManager:
    def __init__(self,store): self.store=store
    @staticmethod
    def check_origin(origin):
        if not re.fullmatch(r'chrome-extension://[a-p]{32}',origin or ''): raise PermissionError('extension origin required')
    def issue_code(self,ttl_seconds=300):
        code=secrets.token_urlsafe(18)
        self.store.put_record('pairing',digest(code),{'expires':time.time()+ttl_seconds,'used':False})
        return code
    def redeem(self,code,origin):
        self.check_origin(origin)
        with self.store.transaction() as db:
            import json
            row=db.execute("SELECT value FROM records WHERE namespace='pairing' AND key=?",(digest(code),)).fetchone()
            value=json.loads(row['value']) if row else None
            if not value or value['used'] or value['expires']<time.time(): raise PermissionError('invalid or expired pairing code')
            value['used']=True
            db.execute("UPDATE records SET value=? WHERE namespace='pairing' AND key=?",(json.dumps(value),digest(code)))
            token=secrets.token_urlsafe(32)
            db.execute('INSERT INTO records VALUES(?,?,?)',('token',digest(token),json.dumps({'origin':origin,'scope':'extension','revoked':False,'expires':time.time()+30*86400})))
        return token
    def verify(self,token,scope,origin):
        row=self.store.record('token',digest(token))
        return bool(row and not row['revoked'] and row['expires']>time.time() and row['scope']==scope and row['origin']==origin)
    def expiry(self,token):
        row=self.store.record('token',digest(token))
        return row['expires'] if row else None
    def revoke(self,token):
        key=digest(token); row=self.store.record('token',key)
        if row: row['revoked']=True; self.store.put_record('token',key,row)
    def rotate(self,token,origin):
        if not self.verify(token,'extension',origin): raise PermissionError('invalid token')
        self.revoke(token)
        return self.redeem(self.issue_code(),origin)
