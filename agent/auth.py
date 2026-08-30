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
PASSWORD_ITERATIONS=600_000


def _b64(data): return base64.urlsafe_b64encode(data).decode().rstrip('=')


def _unb64(value): return base64.urlsafe_b64decode(value+'='*((4-len(value)%4)%4))


def hash_password(password,iterations=PASSWORD_ITERATIONS):
    if not isinstance(password,str) or len(password)<12: raise ValueError('password must be at least 12 characters')
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,int(iterations))
    return f'pbkdf2_sha256${int(iterations)}${_b64(salt)}${_b64(digest)}'


def verify_password(password,encoded):
    try:
        algorithm,iterations,salt,digest=encoded.split('$',3)
        if algorithm!='pbkdf2_sha256': return False
        actual=hashlib.pbkdf2_hmac('sha256',password.encode(),_unb64(salt),int(iterations))
        return hmac.compare_digest(_b64(actual),digest)
    except (AttributeError,TypeError,ValueError): return False


class Authenticator:
    def __init__(self,store=None): self._store=store

    def store(self): return self._store() if callable(self._store) else self._store

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
        try: return bool(self.users()) or bool(self.store() and self.store().list_users())
        except ValueError: return False

    def verify(self,username,password):
        if not isinstance(username,str) or not isinstance(password,str): return False
        store=self.store(); stored=store.get_user(username) if store else None
        if isinstance(stored,dict): return verify_password(password,stored['password_hash'])
        expected=self.users().get(username)
        return expected is not None and hmac.compare_digest(password,expected)

    def exists(self,username):
        store=self.store()
        stored=store.get_user(username) if store else None
        return isinstance(stored,dict) or username in self.users()

    def is_admin(self,username):
        store=self.store(); stored=store.get_user(username) if store else None
        return bool(stored['is_admin']) if isinstance(stored,dict) else username=='thor'

    def credential_version(self,username):
        store=self.store(); stored=store.get_user(username) if store else None
        return stored['updated_at'] if isinstance(stored,dict) else 0

    def list_accounts(self):
        result={name:{'username':name,'is_admin':name=='thor','source':'environment'} for name in self.users()}
        store=self.store()
        if store:
            for user in store.list_users(): result[user['username']]={**user,'is_admin':bool(user['is_admin']),'source':'database'}
        return sorted(result.values(),key=lambda item:item['username'])

    def create_user(self,username,password,is_admin=False):
        if not isinstance(username,str) or not USERNAME_RE.fullmatch(username): raise ValueError('invalid username')
        if not isinstance(is_admin,bool): raise ValueError('is_admin must be a boolean')
        if self.exists(username): raise ValueError('username already exists')
        store=self.store()
        if not store: raise ValueError('user storage is unavailable')
        if not store.save_user(username,hash_password(password),is_admin): raise ValueError('username already exists')

    def change_password(self,username,password):
        store=self.store()
        if not store: raise KeyError(username)
        encoded=hash_password(password)
        if store.update_user_password(username,encoded): return
        if username in self.users() and store.save_user(username,encoded,username=='thor'): return
        raise KeyError(username)

    def delete_user(self,username):
        if username in self.users(): raise ValueError('environment accounts cannot be deleted here')
        store=self.store()
        if not store or not store.delete_user(username): raise KeyError(username)

    def _secret(self):
        secret=os.environ.get('THOR_AUTH_SECRET') or os.environ.get('THOR_MONITOR_PASSWORD','')
        if not secret: raise ValueError('THOR_AUTH_SECRET or THOR_MONITOR_PASSWORD is required')
        return secret.encode()

    def issue(self,username,ttl_seconds=43_200):
        payload=json.dumps({'sub':username,'exp':int(time.time())+max(60,int(ttl_seconds)),'ver':self.credential_version(username),'nonce':secrets.token_hex(8)},separators=(',',':')).encode()
        encoded=_b64(payload); signature=_b64(hmac.new(self._secret(),encoded.encode(),hashlib.sha256).digest())
        return encoded+'.'+signature

    def verify_token(self,token):
        try:
            encoded,signature=token.split('.',1)
            expected=_b64(hmac.new(self._secret(),encoded.encode(),hashlib.sha256).digest())
            if not hmac.compare_digest(signature,expected): return None
            payload=json.loads(_unb64(encoded))
            username=payload.get('sub'); expires=payload.get('exp')
            if not isinstance(username,str) or not self.exists(username) or payload.get('ver')!=self.credential_version(username) or not isinstance(expires,int) or expires<time.time(): return None
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
