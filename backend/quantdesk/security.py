import json
import secrets
from cryptography.fernet import Fernet, InvalidToken
from .config import settings

SECRET_NAMES = {'alpaca_key', 'alpaca_secret', 'tushare_token', 'model_key'}


def cipher():
    key = settings().master_key
    if not key:
        raise ValueError('QD_MASTER_KEY not configured')
    return Fernet(key.encode())


def encrypt(values):
    if set(values) - SECRET_NAMES:
        raise ValueError('unknown credential fields')
    return cipher().encrypt(json.dumps(values).encode()).decode()


def decrypt(value):
    if not value:
        return {}
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except InvalidToken:
        raise ValueError('credential encryption key mismatch') from None


def valid_token(token):
    configured = settings().session_token
    return bool(configured and token and secrets.compare_digest(configured, token))
