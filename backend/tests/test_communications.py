"""Provider contract tests with disposable SQLite and fake network responses."""
import base64
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select
from twilio.request_validator import RequestValidator
from app import communications as c
from app.communication_models import Mailbox, Communication, ContactPermission
from test_workspace import workspace, password_hash, ok

P='/api/workspace/communications'

@pytest.fixture
def configured(monkeypatch):
    for key,value in {'GOOGLE_CLIENT_ID':'test-client','GOOGLE_CLIENT_SECRET':'test-secret','GOOGLE_WORKSPACE_DOMAIN':'workspace.test','COMMUNICATIONS_ENCRYPTION_KEY':Fernet.generate_key().decode(),'COMMUNICATIONS_PUBLIC_URL':'https://testserver','COMMUNICATIONS_SIGNING_KEY':'test-signing-secret','RESEND_WEBHOOK_SECRET':'whsec_'+base64.b64encode(b'test-webhook-secret-32-bytes-long').decode(),'RESEND_API_KEY':'test-resend','CAMPAIGN_FROM_EMAIL':'team@workspace.test','CAMPAIGN_POSTAL_ADDRESS':'123 Test St','TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'test-auth','TWILIO_PHONE_NUMBER':'+16025550100','TWILIO_API_KEY':'SK'+'a'*32,'TWILIO_API_SECRET':'test-api-secret-that-is-at-least-32-bytes','TWILIO_TWIML_APP_SID':'AP'+'a'*32,'TWILIO_SMS_ENABLED':'1'}.items():
        monkeypatch.setenv(key,value)
    calls=[]
    def fake(method,url,**kwargs):
        calls.append((method,url,kwargs))
        if 'oauth2.googleapis.com/token' in url:
            return {'access_token':'fake-access','refresh_token':'fake-refresh','scope':c.GOOGLE_SCOPES}
        if 'userinfo' in url:
            return {'email':'rep_a@workspace.test','email_verified':True,'hd':'workspace.test'}
        if 'Messages.json' in url:
            return {'sid':'SM'+uuid4().hex}
        return {'id':uuid4().hex}
    monkeypatch.setattr(c,'provider',fake)
    return calls


def mailbox(workspace):
    with workspace.session() as db:
        db.add(Mailbox(user_id=workspace.user_ids['rep_a'],email='rep_a@workspace.test',encrypted_token=c.cipher().encrypt(b'test-refresh').decode()));db.commit()


def message(lead,**kw):
    return {'opportunity_id':lead['id'],'command_id':str(uuid4()),'channel':'gmail','subject':'Hello','body':'Checking your project.',**kw}


def permit(client,lead,channel='email'):
    return ok(client.post(P+'/permission',json={'opportunity_id':lead['id'],'channel':channel,'status':'subscribed','evidence':'Customer requested follow-up today.'}))


def test_status_and_role_boundaries(workspace,configured):
    assert ok(workspace.client().get(P+'/status'))['gmail']['connected'] is False
    assert workspace.client('ops').get(P+'/status').status_code==403
    other=workspace.create('rep_b',email='other@example.test')
    assert workspace.client().post(P+'/send',json=message(other)).status_code==404
    shared=workspace.create(visibility='shared',email='shared@example.test')
    assert workspace.client().post(P+'/send',json=message(shared)).status_code==403
    assert not configured


def test_oauth_state_consumed_and_encrypted(workspace,configured):
    client=workspace.client();start=ok(client.post(P+'/gmail/connect'))
    state=parse_qs(urlparse(start['url']).query)['state'][0]
    assert client.get(P+'/gmail/callback',params={'state':'wrong','code':'code'},follow_redirects=False).status_code==400
    # Invalid state consumes the browser state; start over.
    state=parse_qs(urlparse(ok(client.post(P+'/gmail/connect'))['url']).query)['state'][0]
    response=client.get(P+'/gmail/callback',params={'state':state,'code':'code'},follow_redirects=False)
    assert response.status_code==303
    with workspace.session() as db:
        row=db.get(Mailbox,workspace.user_ids['rep_a']);assert 'fake-refresh' not in row.encrypted_token
        assert c.cipher().decrypt(row.encrypted_token.encode())==b'fake-refresh'
    assert client.get(P+'/gmail/callback',params={'state':state,'code':'code'},follow_redirects=False).status_code==400


def test_oauth_allows_authorized_company_mailbox_with_legacy_login(workspace,configured,monkeypatch):
    original=c.provider
    monkeypatch.setattr(c,'provider',lambda method,url,**kw: {'email':'rep_b@workspace.test','email_verified':True,'hd':'workspace.test'} if 'userinfo' in url else original(method,url,**kw))
    client=workspace.client();state=parse_qs(urlparse(ok(client.post(P+'/gmail/connect'))['url']).query)['state'][0]
    assert client.get(P+'/gmail/callback',params={'state':state,'code':'code'},follow_redirects=False).status_code==303
    with workspace.session() as db:
        assert db.get(Mailbox, workspace.user_ids['rep_a']).email == 'rep_b@workspace.test'


def test_gmail_send_is_idempotent_and_scoped(workspace,configured):
    mailbox(workspace);client=workspace.client();lead=workspace.create(email='customer@example.test');data=message(lead)
    first=ok(client.post(P+'/send',json=data));second=ok(client.post(P+'/send',json=data))
    assert first['id']==second['id'];assert first['status']=='accepted'
    assert sum('/messages/send' in url for _,url,_ in configured)==1
    assert client.post(P+'/send',json={**data,'body':'Changed'}).status_code==409
    assert workspace.client('rep_b').get(P+'/history',params={'opportunity_id':lead['id']}).status_code==404
    assert 'fake-access' not in client.get(P+'/history',params={'opportunity_id':lead['id']}).text


def test_unknown_send_never_retried(workspace,configured,monkeypatch):
    mailbox(workspace);lead=workspace.create(email='customer@example.test');data=message(lead);original=c.provider
    def fail(method,url,**kw):
        if '/messages/send' in url:raise HTTPException(502,'Not confirmed')
        return original(method,url,**kw)
    monkeypatch.setattr(c,'provider',fail)
    assert workspace.client().post(P+'/send',json=data).status_code==502
    assert ok(workspace.client().post(P+'/send',json=data))['status']=='unknown'


def test_sms_consent_and_signed_stop(workspace,configured):
    client=workspace.client();lead=workspace.create(phone='602-555-0123');data=message(lead,channel='sms',subject='')
    assert client.post(P+'/send',json=data).status_code==409
    permit(client,lead,'sms');ok(client.post(P+'/send',json=data))
    form={'AccountSid':c.setting('TWILIO_ACCOUNT_SID'),'From':'+16025550123','To':c.setting('TWILIO_PHONE_NUMBER'),'Body':'STOP','MessageSid':'SM-inbound-test'}
    assert client.post(P+'/phone/inbound',data=form).status_code==403
    signature=RequestValidator(c.setting('TWILIO_AUTH_TOKEN')).compute_signature('https://testserver'+P+'/phone/inbound',form)
    for _ in range(2):assert client.post(P+'/phone/inbound',data=form,headers={'X-Twilio-Signature':signature}).status_code==200
    assert client.post(P+'/send',json=message(lead,channel='sms')).status_code==409
    history=ok(client.get(P+'/history',params={'opportunity_id':lead['id']}));assert len(history)==2
    assert client.post(P+'/permission',json={'opportunity_id':lead['id'],'channel':'sms','status':'subscribed','evidence':'Try to override opt-out'}).status_code==409


def campaign(client,ids,audience='subscribers'):
    return ok(client.post(P+'/campaigns',json={'name':'Test draft','audience':audience,'subject':'A project update','body':'Test only','opportunity_ids':ids}))


def test_campaign_preview_suppression_dedupe_and_review(workspace,configured):
    mailbox(workspace)
    client=workspace.client();a=workspace.create(email='customer@example.test');b=workspace.create(email='customer@example.test');d=workspace.create(email='no-consent@example.test');permit(client,a)
    row=campaign(client,[a['id'],b['id'],d['id']]);preview=ok(client.get(P+f'/campaigns/{row["id"]}/preview'))
    assert sum(v['eligible'] for v in preview['recipients'])==1
    assert client.post(P+f'/campaigns/{row["id"]}/send',json={'review_token':'0'*64}).status_code==409
    sent=ok(client.post(P+f'/campaigns/{row["id"]}/send',json={'review_token':preview['review_token']}))
    assert len(sent['messages'])==1
    sent_payload=next(kw['json'] for _,url,kw in configured if url=='https://api.resend.com/emails')
    assert sent_payload['reply_to']=='rep_a@workspace.test'
    assert client.post(P+f'/campaigns/{row["id"]}/send',json={'review_token':preview['review_token']}).status_code==409
    assert sum(url=='https://api.resend.com/emails' for _,url,_ in configured)==1
    assert workspace.client('rep_b').get(P+f'/campaigns/{row["id"]}/preview').status_code==404


def test_prospect_campaign_cannot_use_resend(workspace,configured):
    client=workspace.client();lead=workspace.create(email='prospect@example.test');permit(client,lead)
    row=campaign(client,[lead['id']],'prospects');preview=ok(client.get(P+f'/campaigns/{row["id"]}/preview'))
    assert not preview['recipients'][0]['eligible']
    assert client.post(P+f'/campaigns/{row["id"]}/send',json={'review_token':preview['review_token']}).status_code==409
    assert not configured


def test_unsubscribe_get_does_not_mutate(workspace,configured):
    client=workspace.client();lead=workspace.create(email='customer@example.test');permit(client,lead)
    url=P+'/unsubscribe/'+c.signer().dumps(lead['email'])
    assert client.get(url).status_code==200
    assert ok(client.get(P+'/permission',params={'opportunity_id':lead['id']}))['email']=='subscribed'
    assert client.post(url).status_code==200
    assert ok(client.get(P+'/permission',params={'opportunity_id':lead['id']}))['email']=='blocked'
    assert client.post(P+'/unsubscribe/tampered').status_code==400


def test_call_intent_destination_and_signature(workspace,configured):
    client=workspace.client();lead=workspace.create(phone='6025550123');grant=ok(client.post(P+'/phone/call',json={'opportunity_id':lead['id'],'command_id':str(uuid4())}))
    form={'AccountSid':c.setting('TWILIO_ACCOUNT_SID'),'From':f'client:workspace-{workspace.user_ids["rep_a"]}','CallIntent':str(grant['call_id']),'CallSid':'CA-test-call','To':'+19005550123'}
    endpoint=P+'/phone/voice';signature=RequestValidator(c.setting('TWILIO_AUTH_TOKEN')).compute_signature('https://testserver'+endpoint,form)
    assert client.post(endpoint,data=form).status_code==403
    response=client.post(endpoint,data=form,headers={'X-Twilio-Signature':signature})
    assert response.status_code==200;assert '+16025550123' in response.text;assert '+19005550123' not in response.text
    form['CallSid']='CA-different';signature=RequestValidator(c.setting('TWILIO_AUTH_TOKEN')).compute_signature('https://testserver'+endpoint,form)
    assert '<Reject' in client.post(endpoint,data=form,headers={'X-Twilio-Signature':signature}).text


def test_signed_campaign_bounce_blocks_future_sends(workspace,configured):
    import json
    from datetime import datetime, timezone
    from svix.webhooks import Webhook
    mailbox(workspace)
    client=workspace.client();lead=workspace.create(email='customer@example.test');permit(client,lead)
    row=campaign(client,[lead['id']]);preview=ok(client.get(P+f'/campaigns/{row["id"]}/preview'))
    ok(client.post(P+f'/campaigns/{row["id"]}/send',json={'review_token':preview['review_token']}))
    with workspace.session() as db:
        receipt=db.scalar(select(Communication));provider_id=receipt.provider_id.removeprefix('resend:')
    payload=json.dumps({'type':'email.bounced','data':{'email_id':provider_id}})
    timestamp=datetime.now(timezone.utc);event_id='msg_fake_event'
    signature=Webhook(c.setting('RESEND_WEBHOOK_SECRET')).sign(event_id,timestamp,payload)
    headers={'svix-id':event_id,'svix-timestamp':str(int(timestamp.timestamp())),'svix-signature':signature,'Content-Type':'application/json'}
    assert client.post(P+'/campaigns/events',content=payload).status_code==403
    assert client.post(P+'/campaigns/events',content=payload,headers=headers).status_code==200
    assert ok(client.get(P+'/permission',params={'opportunity_id':lead['id']}))['email']=='blocked'


def test_stale_account_header_rejects_communications(workspace,configured):
    assert workspace.client().get(P+'/status',headers={'X-Workspace-User':str(workspace.user_ids['rep_b'])}).status_code==401
    assert not configured


def test_mailbox_cannot_be_connected_to_two_accounts(workspace,configured):
    with workspace.session() as db:
        db.add(Mailbox(user_id=workspace.user_ids['rep_b'],email='rep_a@workspace.test',encrypted_token=c.cipher().encrypt(b'existing').decode()));db.commit()
    client=workspace.client();state=parse_qs(urlparse(ok(client.post(P+'/gmail/connect'))['url']).query)['state'][0]
    assert client.get(P+'/gmail/callback',params={'state':state,'code':'code'},follow_redirects=False).status_code==409
    with workspace.session() as db:
        assert db.get(Mailbox,workspace.user_ids['rep_a']) is None


def test_mailbox_audit_does_not_retain_encrypted_grant(workspace,configured):
    from app.models import AuditLog
    client=workspace.client();state=parse_qs(urlparse(ok(client.post(P+'/gmail/connect'))['url']).query)['state'][0]
    assert client.get(P+'/gmail/callback',params={'state':state,'code':'code'},follow_redirects=False).status_code==303
    with workspace.session() as db:
        audit=db.scalar(select(AuditLog).where(AuditLog.table_name=='workspace_mailboxes'))
        assert audit.changes['encrypted_token']==['***','***']


def test_campaign_requires_connected_reply_mailbox(workspace,configured):
    client=workspace.client();lead=workspace.create(email='customer@example.test');permit(client,lead)
    row=campaign(client,[lead['id']]);preview=ok(client.get(P+f'/campaigns/{row["id"]}/preview'))
    response=client.post(P+f'/campaigns/{row["id"]}/send',json={'review_token':preview['review_token']})
    assert response.status_code==409
    assert 'Gmail' in response.json()['detail']
    assert not configured
