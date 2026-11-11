"""Opt-in device push for new website inquiries; no customer data in payloads."""
import asyncio
import base64
import hashlib
import json
import logging
import os
from urllib.parse import urlsplit
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import BigInteger, ForeignKey, String, Text, select, func
from sqlalchemy.orm import Mapped, mapped_column, Session
from sqlalchemy.exc import IntegrityError
import requests
from pywebpush import webpush, WebPushException
from .db import Base, SessionLocal, get_db
from .auth import current_user
from .models import User
from .communications import cipher
from .website_leads import WebsiteLeadReceipt
from .workspace_models import Opportunity

router = APIRouter(prefix='/api/workspace/push', tags=['push'])
log = logging.getLogger(__name__)

class PushConfig(Base):
    __tablename__ = 'workspace_push_config'
    id: Mapped[int] = mapped_column(primary_key=True)
    secret: Mapped[str] = mapped_column(Text)
    public_key: Mapped[str] = mapped_column(Text)

class PushDevice(Base):
    __tablename__ = 'workspace_push_devices'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    endpoint_hash: Mapped[str] = mapped_column(String(64), unique=True)
    encrypted_subscription: Mapped[str] = mapped_column(Text)
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0)


def sales(user):
    if user.role not in ('owner', 'rep'):
        raise HTTPException(403, 'Sales access required.')

def key_config(db):
    row = db.get(PushConfig, 1)
    if not row:
        key = ec.generate_private_key(ec.SECP256R1())
        private = base64.urlsafe_b64encode(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode().rstrip('=')
        public = base64.urlsafe_b64encode(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode().rstrip('=')
        row = PushConfig(id=1, secret=cipher().encrypt(private.encode()).decode(), public_key=public)
        db.add(row)
        try: db.commit()
        except IntegrityError:
            db.rollback(); row = db.get(PushConfig, 1)
    return row

class Endpoint(BaseModel):
    endpoint: str = Field(max_length=4096)

    @field_validator('endpoint')
    @classmethod
    def trusted_push_service(cls, value):
        u = urlsplit(value)
        # Never send server-side requests or VAPID credentials to arbitrary hosts.
        if u.scheme != 'https' or u.hostname not in ('fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com') or u.port not in (None, 443) or u.username or u.password or u.fragment:
            raise ValueError('Unsupported push service. Use Safari, Chrome, or Firefox.')
        return value

class Subscription(Endpoint):
    keys: dict[str, str]

    @field_validator('keys')
    @classmethod
    def valid_keys(cls, keys):
        try:
            auth = base64.urlsafe_b64decode(keys['auth'] + '===')
            point = base64.urlsafe_b64decode(keys['p256dh'] + '===')
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
            if len(auth) != 16: raise ValueError()
        except Exception: raise ValueError('Invalid device subscription keys') from None
        return {k: keys[k] for k in ('auth', 'p256dh')}

@router.get('/config')
def config(db: Session = Depends(get_db), user: User = Depends(current_user)):
    sales(user)
    return {'public_key': key_config(db).public_key}

@router.post('/subscribe')
def subscribe(body: Subscription, db: Session = Depends(get_db), user: User = Depends(current_user)):
    sales(user); key_config(db)
    digest = hashlib.sha256(body.endpoint.encode()).hexdigest()
    row = db.scalar(select(PushDevice).where(PushDevice.endpoint_hash == digest))
    if row and row.user_id != user.id:
        raise HTTPException(409, 'This device belongs to another account. Turn off device alerts before switching accounts.')
    if not row:
        count = db.scalar(select(func.count()).select_from(PushDevice).where(PushDevice.user_id == user.id))
        if count >= 10: raise HTTPException(409, 'Device limit reached. Ask your administrator for help.')
        row = PushDevice(user_id=user.id, endpoint_hash=digest, last_sequence=db.scalar(select(func.max(WebsiteLeadReceipt.source_sequence))) or 0)
    row.encrypted_subscription = cipher().encrypt(json.dumps(body.model_dump()).encode()).decode()
    db.add(row); db.commit()
    return {'enabled': True}

@router.post('/unsubscribe')
def unsubscribe(body: Endpoint, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.scalar(select(PushDevice).where(PushDevice.endpoint_hash == hashlib.sha256(body.endpoint.encode()).hexdigest(), PushDevice.user_id == user.id))
    if row: db.delete(row); db.commit()
    return {'enabled': False}

class NoRedirectSession(requests.Session):
    def request(self, *args, **kwargs):
        kwargs['allow_redirects'] = False
        return super().request(*args, **kwargs)

def deliver(row, cfg, test=False):
    subscription = json.loads(cipher().decrypt(row.encrypted_subscription.encode()))
    Endpoint(endpoint=subscription['endpoint'])
    with NoRedirectSession() as session:
        response = webpush(subscription_info=subscription, data=json.dumps({'title':'Mill City · Phone alerts ready' if test else 'Mill City · New website inquiry', 'body':'Test received. Your phone can now receive website inquiry alerts.' if test else 'A new inquiry needs an owner. Open the shared lead pool to claim it.', 'tag':'mill-city-test' if test else 'mill-city-website', 'url':'/#/sales?scope=shared'}), vapid_private_key=cipher().decrypt(cfg.secret.encode()).decode(), vapid_claims={'sub':'mailto:info@millcitycabinetry.com'}, ttl=300, timeout=10, requests_session=session)
        if not 200 <= response.status_code < 300: raise RuntimeError('Push delivery rejected')

@router.post('/test')
def test_push(body: Endpoint, db: Session = Depends(get_db), user: User = Depends(current_user)):
    sales(user)
    row = db.scalar(select(PushDevice).where(PushDevice.endpoint_hash == hashlib.sha256(body.endpoint.encode()).hexdigest(), PushDevice.user_id == user.id))
    if not row: raise HTTPException(404, 'Enable phone alerts on this device first.')
    try: deliver(row, key_config(db), test=True)
    except Exception: raise HTTPException(502, 'The test could not be delivered. Disable and re-enable phone alerts, then try again.') from None
    return {'sent':True}

def send_pending(sessions=SessionLocal, sender=deliver):
    with sessions() as db:
        ids = list(db.scalars(select(PushDevice.id)))
    for id in ids:
        with sessions() as db:
            row = db.scalar(select(PushDevice).where(PushDevice.id == id).with_for_update(skip_locked=True))
            if not row: continue
            user = db.get(User, row.user_id)
            if not user or not user.active or user.role not in ('owner','rep'):
                db.delete(row); db.commit(); continue
            latest = db.scalar(select(func.max(WebsiteLeadReceipt.source_sequence))) or 0
            if latest <= row.last_sequence: continue
            pending = db.scalar(select(WebsiteLeadReceipt.source_sequence).join(Opportunity, Opportunity.id == WebsiteLeadReceipt.opportunity_id).where(WebsiteLeadReceipt.source_sequence > row.last_sequence, Opportunity.visibility == 'shared', Opportunity.stage.notin_(('Won','Lost'))).limit(1))
            try:
                if pending: sender(row, db.get(PushConfig, 1))
                row.last_sequence = latest; db.commit()
            except WebPushException as e:
                if e.response is not None and e.response.status_code in (404,410):
                    db.delete(row); db.commit()
                else: log.warning('Phone alert delivery pending; retrying later')
            except Exception: log.warning('Phone alert delivery pending; retrying later')

async def run():
    while True:
        try: await asyncio.to_thread(send_pending)
        except asyncio.CancelledError: raise
        except Exception: log.warning('Phone alert worker will retry')
        await asyncio.sleep(30)


@router.post('/status')
def device_status(body: Endpoint, db: Session = Depends(get_db), user: User = Depends(current_user)):
    sales(user)
    row = db.scalar(select(PushDevice.id).where(PushDevice.endpoint_hash == hashlib.sha256(body.endpoint.encode()).hexdigest(), PushDevice.user_id == user.id))
    return {'enabled': row is not None}
