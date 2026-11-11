import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from sqlalchemy import select
from py_vapid import Vapid
from app import push_notifications as p, website_leads as w
from test_workspace import workspace, password_hash, ok, command
from test_website_leads import item


def subscription():
    public = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    enc=lambda b:base64.urlsafe_b64encode(b).decode().rstrip('=')
    return {'endpoint':'https://fcm.googleapis.com/fcm/send/example-test-only','keys':{'auth':enc(b'0123456789abcdef'),'p256dh':enc(public)}}

def setup(workspace, monkeypatch):
    monkeypatch.setenv('COMMUNICATIONS_ENCRYPTION_KEY',Fernet.generate_key().decode())
    a=workspace.client('rep_a'); body=subscription()
    ok(a.post('/api/workspace/push/subscribe',json=body))
    return a,body

def test_roles_validation_and_encrypted_keys(workspace,monkeypatch):
    a,body=setup(workspace,monkeypatch)
    assert workspace.client('designer').get('/api/workspace/push/config').status_code==403
    bad={**body,'endpoint':'https://127.0.0.1/private'}
    assert a.post('/api/workspace/push/subscribe',json=bad).status_code==422
    bad={**body,'endpoint':'https://fcm.googleapis.com.evil.test/send'}
    assert a.post('/api/workspace/push/subscribe',json=bad).status_code==422
    b=workspace.client('rep_b')
    assert b.post('/api/workspace/push/subscribe',json=body).status_code==409
    assert ok(b.post('/api/workspace/push/status',json={'endpoint':body['endpoint']}))=={'enabled':False}
    with workspace.session() as db:
        cfg=db.get(p.PushConfig,1)
        Vapid.from_string(p.cipher().decrypt(cfg.secret.encode()).decode())
        row=db.scalar(select(p.PushDevice))
        assert body['endpoint'] not in row.encrypted_subscription
    ok(a.post('/api/workspace/push/unsubscribe',json={'endpoint':body['endpoint']}))
    assert ok(a.post('/api/workspace/push/status',json={'endpoint':body['endpoint']}))=={'enabled':False}

def test_retry_dedupe_and_claimed_suppression(workspace,monkeypatch):
    a,body=setup(workspace,monkeypatch)
    calls=[]
    w.ingest([item(1)],workspace.session)
    def fail(*args): raise RuntimeError('temporary outage')
    p.send_pending(workspace.session,fail)
    with workspace.session() as db: assert db.scalar(select(p.PushDevice)).last_sequence==0
    p.send_pending(workspace.session,lambda *args:calls.append(1))
    p.send_pending(workspace.session,lambda *args:calls.append(1))
    assert calls==[1]
    w.ingest([item(2)],workspace.session)
    leads=ok(a.get('/api/workspace/opportunities?scope=shared'))
    for lead in leads: ok(a.post(f"/api/workspace/opportunities/{lead['id']}/claim",json=command(lead['revision'])))
    p.send_pending(workspace.session,lambda *args:calls.append(1))
    assert calls==[1]
    with workspace.session() as db: assert db.scalar(select(p.PushDevice)).last_sequence==2

def test_disabled_rep_devices_removed(workspace,monkeypatch):
    a,body=setup(workspace,monkeypatch)
    with workspace.session() as db:
        user=db.get(p.User,workspace.user_ids['rep_a']); user.active=False; db.commit()
    p.send_pending(workspace.session,lambda *args: (_ for _ in ()).throw(AssertionError('must not send')))
    with workspace.session() as db: assert db.scalar(select(p.PushDevice)) is None

def test_real_encryption_builds_push_without_customer_data(workspace,monkeypatch):
    a,body=setup(workspace,monkeypatch)
    sent=[]
    def request(self,method,url,**kwargs):
        assert url==body['endpoint']
        assert kwargs['timeout']==10
        assert kwargs['headers'].get('authorization') or kwargs['headers'].get('Authorization')
        assert b'example-test-only' not in kwargs['data']
        sent.append(1)
        import requests
        response=requests.Response();response.status_code=201
        return response
    monkeypatch.setattr(p.NoRedirectSession,'request',request)
    ok(a.post('/api/workspace/push/test',json={'endpoint':body['endpoint']}))
    assert sent==[1]
