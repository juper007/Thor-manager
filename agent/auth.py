import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time


USERNAME_RE=re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
COOKIE_NAME='thor_session'


def _b64(data): return base64.urlsafe_b64encode(data).decode().rstrip('=')


def _unb64(value): return base64.urlsafe_b64decode(value+'='*((4-len(value)%4)%4))


class Authenticator:
    def users(self):
        users={}
        legacy=os.environ.get('THOR_MONITOR_PASSWORD','')
        if legacy: users['thor']=legacy
        raw=os.environ.get('THOR_MONITOR_USERS_JSON','').strip()
        if raw:
            try: configured=json.loads(raw)
            except json.JSONDecodeError as exc: raise ValueError('THOR_MONITOR_USERS_JSON must be valid JSON') from exc
            if not isinstance(configured,dict): raise ValueError('THOR_MONITOR_USERS_JSON must be a JSON object')
            for username,password in configured.items():
                if not isinstance(username,str) or not USERNAME_RE.fullmatch(username) or not isinstance(password,str) or not password:
                    raise ValueError('configured users require valid usernames and non-empty string passwords')
                users[username]=password
        return users

    def configured(self):
        try: return bool(self.users())
        except ValueError: return False

    def verify(self,username,password):
        if not isinstance(username,str) or not isinstance(password,str): return False
        expected=self.users().get(username)
        return expected is not None and hmac.compare_digest(password,expected)

    def _secret(self):
        secret=os.environ.get('THOR_AUTH_SECRET') or os.environ.get('THOR_MONITOR_PASSWORD','')
        if not secret: raise ValueError('THOR_AUTH_SECRET or THOR_MONITOR_PASSWORD is required')
        return secret.encode()

    def issue(self,username,ttl_seconds=43_200):
        payload=json.dumps({'sub':username,'exp':int(time.time())+max(60,int(ttl_seconds)),'nonce':secrets.token_hex(8)},separators=(',',':')).encode()
        encoded=_b64(payload); signature=_b64(hmac.new(self._secret(),encoded.encode(),hashlib.sha256).digest())
        return encoded+'.'+signature

    def verify_token(self,token):
        try:
            encoded,signature=token.split('.',1)
            expected=_b64(hmac.new(self._secret(),encoded.encode(),hashlib.sha256).digest())
            if not hmac.compare_digest(signature,expected): return None
            payload=json.loads(_unb64(encoded))
            username=payload.get('sub'); expires=payload.get('exp')
            if not isinstance(username,str) or username not in self.users() or not isinstance(expires,int) or expires<time.time(): return None
            return username
        except (ValueError,TypeError,json.JSONDecodeError): return None

    def identify(self,authorization='',cookie_header=''):
        if authorization.startswith('Basic '):
            try:
                username,password=base64.b64decode(authorization[6:],validate=True).decode().split(':',1)
                if self.verify(username,password): return username
            except (ValueError,UnicodeDecodeError): pass
        for item in cookie_header.split(';'):
            name,separator,value=item.strip().partition('=')
            if separator and name==COOKIE_NAME: return self.verify_token(value)
        return None

    def cookie(self,token,max_age=43_200):
        secure='; Secure' if os.environ.get('THOR_AUTH_COOKIE_SECURE','0')=='1' else ''
        return f'{COOKIE_NAME}={token}; Path=/; Max-Age={int(max_age)}; HttpOnly; SameSite=Strict{secure}'

    def clear_cookie(self): return f'{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict'
