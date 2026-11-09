"""Account-bound OAuth, explicit delivery, and durable provider receipts.

No background campaign sending. Unknown delivery results are never auto-retried.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import time
from email.message import EmailMessage
from typing import Literal
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse, Response
from itsdangerous import URLSafeSerializer, BadSignature
from pydantic import Field
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from svix.webhooks import Webhook, WebhookVerificationError
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from .auth import current_user
from .db import get_db
from .models import User, now
from .workspace import Input, SALES, _role, _read_opportunity, _edit_opportunity, _sales_filter
from .workspace_models import Opportunity, Activity
from .communication_models import Mailbox, OAuthAttempt, Communication, ContactPermission, Campaign

router = APIRouter(prefix='/api/workspace/communications', tags=['communications'])
GOOGLE_SCOPES = 'openid email https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly'


def setting(key):
    return os.environ.get(key, '').strip()


def require_settings(*keys):
    missing = [key for key in keys if not setting(key)]
    if missing:
        raise HTTPException(503, 'Administrator setup needed: ' + ', '.join(missing))


def origin():
    require_settings('COMMUNICATIONS_PUBLIC_URL')
    value = setting('COMMUNICATIONS_PUBLIC_URL').rstrip('/')
    if not re.fullmatch(r'https://[a-zA-Z0-9.-]+(?::[0-9]+)?', value):
        raise HTTPException(503, 'Communications public URL must be an HTTPS origin.')
    return value


def cipher():
    require_settings('COMMUNICATIONS_ENCRYPTION_KEY')
    try:
        return Fernet(setting('COMMUNICATIONS_ENCRYPTION_KEY').encode())
    except ValueError:
        raise HTTPException(503, 'Mailbox encryption key needs administrator setup.') from None


def provider(method, url, **kwargs):
    """Never expose tokens, URLs, or provider response bodies in errors/logs."""
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'Provider request was not confirmed. Check the provider before trying another send.') from None


def email(value):
    value = (value or '').strip().lower()
    if not re.fullmatch(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', value):
        raise HTTPException(422, 'This lead needs a valid email address.')
    return value


def phone(value):
    value = re.sub(r'[\s().-]', '', value or '')
    if len(value) == 10 and value.isdigit():
        value = '+1' + value
    if len(value) == 11 and value.startswith('1'):
        value = '+' + value
    if not re.fullmatch(r'\+1[2-9][0-9]{9}', value):
        raise HTTPException(422, 'Use a valid US/Canada number for this pilot.')
    return value


def permission(db, channel, address):
    return db.scalar(select(ContactPermission).where(ContactPermission.channel == channel, ContactPermission.address == address))


def allowed(db, channel, address, optin=False):
    row = permission(db, channel, address)
    if row and row.status == 'blocked':
        raise HTTPException(409, 'This contact has opted out. Sending is blocked.')
    if optin and (not row or row.status != 'subscribed'):
        raise HTTPException(409, 'Record contact permission before sending this message.')


def public_message(row):
    return {key: getattr(row, key) for key in ('id','opportunity_id','channel','direction','recipient','subject','body','status','created_at')}


@router.get('/status')
def status(db: Session = Depends(get_db), user=Depends(current_user)):
    _role(user, SALES)
    mailbox = db.get(Mailbox, user.id)
    groups = {
        'gmail': ['GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','GOOGLE_WORKSPACE_DOMAIN','COMMUNICATIONS_ENCRYPTION_KEY','COMMUNICATIONS_PUBLIC_URL'],
        'phone': ['TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_API_KEY','TWILIO_API_SECRET','TWILIO_TWIML_APP_SID','TWILIO_PHONE_NUMBER','COMMUNICATIONS_PUBLIC_URL'],
        'campaigns': ['RESEND_API_KEY','RESEND_WEBHOOK_SECRET','CAMPAIGN_FROM_EMAIL','CAMPAIGN_POSTAL_ADDRESS','COMMUNICATIONS_PUBLIC_URL','COMMUNICATIONS_SIGNING_KEY'],
    }
    result = {key: {'configured': all(setting(v) for v in keys), 'missing': [v for v in keys if not setting(v)] if user.role == 'owner' else []} for key, keys in groups.items()}
    result['gmail'].update({'connected': bool(mailbox), 'email': mailbox.email if mailbox else None})
    result['phone'].update({'number': setting('TWILIO_PHONE_NUMBER'), 'sms_enabled': setting('TWILIO_SMS_ENABLED') == '1'})
    result['prospects'] = {'configured': False, 'detail': 'Separate prospect provider setup is required. Resend is only for subscribed audiences.'}
    return result


@router.post('/gmail/connect')
def connect(request: Request, db: Session = Depends(get_db), user=Depends(current_user)):
    _role(user, SALES)
    require_settings('GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','GOOGLE_WORKSPACE_DOMAIN')
    cipher()
    state = secrets.token_urlsafe(32)
    request.session['gmail_oauth'] = state
    db.execute(delete(OAuthAttempt).where(OAuthAttempt.expires < int(time.time())))
    db.add(OAuthAttempt(state_hash=hashlib.sha256(state.encode()).hexdigest(),user_id=user.id, expires=int(time.time())+600))
    db.commit()
    return {'url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({'client_id':setting('GOOGLE_CLIENT_ID'),'redirect_uri':origin()+'/api/workspace/communications/gmail/callback','response_type':'code','scope':GOOGLE_SCOPES,'access_type':'offline','prompt':'consent select_account','state':state,'hd':setting('GOOGLE_WORKSPACE_DOMAIN')})}


@router.get('/gmail/callback')
def callback(request: Request, state: str = '', code: str = '', error: str = '', db: Session = Depends(get_db), user=Depends(current_user)):
    _role(user, SALES)
    expected = request.session.pop('gmail_oauth', '')
    if not state or not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(400, 'Gmail connection expired. Start again from Communications.')
    claimed = db.execute(delete(OAuthAttempt).where(OAuthAttempt.state_hash == hashlib.sha256(state.encode()).hexdigest(), OAuthAttempt.user_id == user.id, OAuthAttempt.expires >= int(time.time())))
    db.commit()
    if claimed.rowcount != 1 or error or not code:
        return RedirectResponse('/#/communications?gmail=cancelled', status_code=303)
    token = provider('POST','https://oauth2.googleapis.com/token',data={'code':code,'client_id':setting('GOOGLE_CLIENT_ID'),'client_secret':setting('GOOGLE_CLIENT_SECRET'),'redirect_uri':origin()+'/api/workspace/communications/gmail/callback','grant_type':'authorization_code'})
    if not set(GOOGLE_SCOPES.split()).difference({'openid','email'}) <= set(token.get('scope','').split()):
        raise HTTPException(400,'Approve both email reading and sending to connect your mailbox.')
    account = provider('GET','https://openidconnect.googleapis.com/v1/userinfo',headers={'Authorization':'Bearer '+token['access_token']})
    if account.get('hd','').lower() != setting('GOOGLE_WORKSPACE_DOMAIN').lower() or not account.get('email_verified'):
        raise HTTPException(403,'Connect your company Google Workspace mailbox.')
    address = email(account.get('email'))
    # OAuth state binds this grant to the signed-in workspace account. Legacy
    # Job Tracker logins need not use the same address as the company mailbox.
    other = db.scalar(select(Mailbox).where(Mailbox.email == address, Mailbox.user_id != user.id))
    if other:
        raise HTTPException(409, 'This mailbox is already connected to another workspace account.')
    previous = db.get(Mailbox, user.id)
    if previous and previous.email != address:
        raise HTTPException(409, 'Disconnect your current mailbox before choosing a different mailbox.')
    if not token.get('refresh_token'):
        raise HTTPException(400,'Google did not provide offline access. Reconnect and approve access.')
    row = db.get(Mailbox,user.id) or Mailbox(user_id=user.id)
    row.email = address
    row.encrypted_token = cipher().encrypt(token['refresh_token'].encode()).decode()
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'This mailbox was just connected elsewhere. Review your connections.') from None
    return RedirectResponse('/#/communications?gmail=connected',status_code=303)


def gmail_token(db, user):
    row = db.get(Mailbox,user.id)
    if not row:
        raise HTTPException(409,'Connect your Gmail mailbox first.')
    try:
        refresh = cipher().decrypt(row.encrypted_token.encode()).decode()
    except InvalidToken:
        raise HTTPException(503,'Mailbox encryption key changed. Reconnect after administrator review.') from None
    result = provider('POST','https://oauth2.googleapis.com/token',data={'client_id':setting('GOOGLE_CLIENT_ID'),'client_secret':setting('GOOGLE_CLIENT_SECRET'),'refresh_token':refresh,'grant_type':'refresh_token'})
    return result['access_token']


@router.post('/gmail/disconnect')
def disconnect(db: Session = Depends(get_db), user=Depends(current_user)):
    _role(user, SALES)
    row = db.get(Mailbox,user.id)
    if row:
        # Revoke Google grant before removing local credentials; retain it if revocation fails.
        token = cipher().decrypt(row.encrypted_token.encode()).decode()
        provider('POST','https://oauth2.googleapis.com/revoke',data={'token':token},headers={'Content-Type':'application/x-www-form-urlencoded'})
        db.delete(row)
        db.commit()
    return {'disconnected':True}


@router.get('/gmail/messages')
def gmail_messages(opportunity_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    _role(user,SALES)
    lead = _edit_opportunity(db,user,opportunity_id)
    address = email(lead.email)
    headers = {'Authorization':'Bearer '+gmail_token(db,user)}
    found = provider('GET','https://gmail.googleapis.com/gmail/v1/users/me/messages',headers=headers,params={'q':f'from:({address}) OR to:({address})','maxResults':20})
    rows = []
    for item in found.get('messages',[]):
        data = provider('GET','https://gmail.googleapis.com/gmail/v1/users/me/messages/'+item['id'],headers=headers,params={'format':'full'})
        meta = {v['name'].lower():v['value'] for v in data.get('payload',{}).get('headers',[])}
        def plain(part):
            if part.get('mimeType') == 'text/plain' and part.get('body',{}).get('data'):
                value=part['body']['data']; return base64.urlsafe_b64decode(value+'='*(-len(value)%4)).decode('utf-8',errors='replace')[:30000]
            return '\n'.join(plain(child) for child in part.get('parts',[]))
        rows.append({'id':data['id'],'subject':meta.get('subject',''),'from':meta.get('from',''),'date':meta.get('date',''),'body':plain(data.get('payload',{})) or data.get('snippet','')})
    return rows


class MessageIn(Input):
    opportunity_id: int = Field(gt=0)
    command_id: str = Field(min_length=8,max_length=120)
    channel: Literal['gmail','sms']
    subject: str = Field(default='',max_length=200,pattern=r'^[^\r\n]*$')
    body: str = Field(min_length=1,max_length=20000)


def reserve(db,user,lead,command_id,channel,recipient,subject,body):
    existing = db.scalar(select(Communication).where(Communication.actor_id==user.id,Communication.command_id==command_id))
    if existing:
        if (existing.opportunity_id,existing.channel,existing.recipient,existing.subject,existing.body) != (lead.id,channel,recipient,subject,body):
            raise HTTPException(409,'This send request was already used with different content.')
        return existing, False
    row=Communication(actor_id=user.id,opportunity_id=lead.id,command_id=command_id,channel=channel,recipient=recipient,subject=subject,body=body,status='sending')
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409,'This send is already being processed. Refresh message history.') from None
    return row,True


def finish(db,row,provider_id):
    db.refresh(row)
    row.provider_id=provider_id
    if row.status in ('sending', 'unknown'):
        row.status='accepted'
    db.add(Activity(opportunity_id=row.opportunity_id,actor_id=row.actor_id,kind='Email' if row.channel in ('gmail','campaign') else 'Text',body=f'{row.channel} accepted by provider: {row.subject or row.body[:180]}'))
    db.commit()
    return public_message(row)


@router.post('/send')
def send(data: MessageIn,db: Session=Depends(get_db),user=Depends(current_user)):
    _role(user,SALES)
    lead=_edit_opportunity(db,user,data.opportunity_id)
    target=email(lead.email) if data.channel=='gmail' else phone(lead.phone)
    allowed(db,'email' if data.channel=='gmail' else 'sms',target,optin=data.channel=='sms')
    if data.channel=='gmail':
        token=gmail_token(db,user)
    else:
        require_settings('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_PHONE_NUMBER')
        if setting('TWILIO_SMS_ENABLED')!='1':
            raise HTTPException(503,'Texting awaits number registration and administrator activation.')
        if len(data.body)>1600:
            raise HTTPException(422,'Text messages must be 1,600 characters or fewer.')
    row,fresh=reserve(db,user,lead,data.command_id,data.channel,target,data.subject,data.body)
    if not fresh:
        return public_message(row)
    try:
        if data.channel=='gmail':
            msg=EmailMessage(); msg['To']=target; msg['From']=db.get(Mailbox,user.id).email; msg['Subject']=data.subject; msg.set_content(data.body)
            result=provider('POST','https://gmail.googleapis.com/gmail/v1/users/me/messages/send',headers={'Authorization':'Bearer '+token},json={'raw':base64.urlsafe_b64encode(msg.as_bytes()).decode()})
            return finish(db,row,'gmail:'+str(user.id)+':'+result['id'])
        result=provider('POST',f'https://api.twilio.com/2010-04-01/Accounts/{setting("TWILIO_ACCOUNT_SID")}/Messages.json',auth=(setting('TWILIO_ACCOUNT_SID'),setting('TWILIO_AUTH_TOKEN')),data={'To':target,'From':setting('TWILIO_PHONE_NUMBER'),'Body':data.body,'StatusCallback':origin()+'/api/workspace/communications/phone/status?receipt='+str(row.id)})
        return finish(db,row,result['sid'])
    except HTTPException:
        row.status='unknown'; db.commit(); raise


@router.get('/history')
def history(opportunity_id:int,db: Session=Depends(get_db),user=Depends(current_user)):
    _role(user,SALES); _read_opportunity(db,user,opportunity_id)
    return [public_message(row) for row in db.scalars(select(Communication).where(Communication.opportunity_id==opportunity_id,Communication.actor_id==user.id).order_by(Communication.id.desc()).limit(100))]


class PermissionIn(Input):
    opportunity_id:int=Field(gt=0)
    channel:Literal['email','sms']
    status:Literal['subscribed','blocked']
    evidence:str=Field(min_length=10,max_length=2000)


@router.post('/permission')
def set_permission(data:PermissionIn,db: Session=Depends(get_db),user=Depends(current_user)):
    _role(user,SALES); lead=_edit_opportunity(db,user,data.opportunity_id)
    address=email(lead.email) if data.channel=='email' else phone(lead.phone)
    row=permission(db,data.channel,address)
    if row and row.status=='blocked' and data.status=='subscribed':
        raise HTTPException(409,'An opt-out cannot be overridden here. Obtain a fresh provider subscription.')
    row=row or ContactPermission(channel=data.channel,address=address)
    row.status=data.status;row.evidence=data.evidence;row.actor_id=user.id;row.updated_at=now();db.add(row)
    db.add(Activity(opportunity_id=lead.id,actor_id=user.id,kind='Note',body=f'{data.channel} permission {data.status}: {data.evidence}'))
    db.commit();return {'status':row.status}


@router.get('/permission')
def get_permission(opportunity_id:int,db:Session=Depends(get_db),user=Depends(current_user)):
    _role(user,SALES); lead=_read_opportunity(db,user,opportunity_id); result={}
    for channel,value,normalize in [('email',lead.email,email),('sms',lead.phone,phone)]:
        try:
            row=permission(db,channel,normalize(value));result[channel]=row.status if row else 'unknown'
        except HTTPException:
            result[channel]='missing_contact'
    return result


class CallIn(Input):
    opportunity_id:int=Field(gt=0)
    command_id:str=Field(min_length=8,max_length=120)


@router.post('/phone/call')
def call(data:CallIn,db:Session=Depends(get_db),user=Depends(current_user)):
    _role(user,SALES);lead=_edit_opportunity(db,user,data.opportunity_id)
    require_settings('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_API_KEY','TWILIO_API_SECRET','TWILIO_TWIML_APP_SID','TWILIO_PHONE_NUMBER')
    target=phone(lead.phone)
    row,fresh=reserve(db,user,lead,data.command_id,'call',target,'','')
    if not fresh:
        raise HTTPException(409,'This call request already exists. Start a new call intentionally.')
    row.status='ready';db.commit()
    token=AccessToken(setting('TWILIO_ACCOUNT_SID'),setting('TWILIO_API_KEY'),setting('TWILIO_API_SECRET'),identity=f'workspace-{user.id}',ttl=600)
    token.add_grant(VoiceGrant(outgoing_application_sid=setting('TWILIO_TWIML_APP_SID'),incoming_allow=False))
    return {'token':token.to_jwt(),'call_id':row.id,'recipient':target}


async def signed_phone(request):
    require_settings('TWILIO_AUTH_TOKEN')
    form=await request.form()
    url=origin()+request.url.path
    if request.url.query:
        url+='?'+request.url.query
    if not RequestValidator(setting('TWILIO_AUTH_TOKEN')).validate(url,form,request.headers.get('X-Twilio-Signature','')):
        raise HTTPException(403,'Invalid provider signature.')
    if form.get('AccountSid') != setting('TWILIO_ACCOUNT_SID'):
        raise HTTPException(403,'Unexpected provider account.')
    return form


@router.post('/phone/voice')
async def voice(request:Request,db:Session=Depends(get_db)):
    form=await signed_phone(request)
    try:
        row=db.get(Communication,int(form.get('CallIntent','0')))
    except ValueError:
        row=None
    actor=db.get(User,row.actor_id) if row else None
    response=VoiceResponse()
    if not row or row.channel!='call' or not actor or not actor.active or actor.role not in SALES or form.get('From')!=f'client:workspace-{row.actor_id}':
        response.reject();return Response(str(response),media_type='application/xml')
    _edit_opportunity(db,actor,row.opportunity_id)
    # A call token cannot select arbitrary destinations or replay a completed intent.
    if row.provider_id==form.get('CallSid'):
        pass
    elif row.status!='ready' or (now()-row.created_at.replace(tzinfo=now().tzinfo)).total_seconds()>600:
        response.reject();return Response(str(response),media_type='application/xml')
    else:
        changed=db.execute(update(Communication).where(Communication.id==row.id,Communication.status=='ready').values(status='dialing',provider_id=form.get('CallSid')))
        if changed.rowcount!=1:
            response.reject();return Response(str(response),media_type='application/xml')
        db.add(Activity(opportunity_id=row.opportunity_id,actor_id=row.actor_id,kind='Call',body='Started browser call to '+row.recipient));db.commit()
    dial=response.dial(caller_id=setting('TWILIO_PHONE_NUMBER'),time_limit=3600,action=origin()+'/api/workspace/communications/phone/call-complete')
    dial.number(row.recipient)
    return Response(str(response),media_type='application/xml')


@router.post('/phone/call-complete')
async def call_complete(request:Request,db:Session=Depends(get_db)):
    form=await signed_phone(request)
    row=db.scalar(select(Communication).where(Communication.provider_id==form.get('CallSid'),Communication.channel=='call'))
    if row:
        state=form.get('DialCallStatus','unknown')
        if state in ('completed','busy','no-answer','failed','canceled'):
            row.status=state;db.commit()
    return Response('<Response><Hangup/></Response>',media_type='application/xml')


@router.post('/phone/status')
async def phone_status(request:Request,db:Session=Depends(get_db)):
    form=await signed_phone(request)
    try:
        receipt_id=int(request.query_params.get('receipt','0'))
    except ValueError:
        raise HTTPException(400,'Invalid receipt.') from None
    row=db.get(Communication,receipt_id)
    if not row or row.channel!='sms' or row.recipient!=form.get('To') or form.get('From')!=setting('TWILIO_PHONE_NUMBER') or (row.provider_id and row.provider_id!=form.get('MessageSid')):
        raise HTTPException(403,'Unexpected delivery receipt.')
    row.provider_id=form.get('MessageSid')
    state=form.get('MessageStatus')
    if row and state in ('delivered','undelivered','failed') and row.status not in ('delivered','undelivered','failed'):
        row.status=state;db.commit()
    return Response(status_code=204)


@router.post('/phone/inbound')
async def inbound(request:Request,db:Session=Depends(get_db)):
    form=await signed_phone(request)
    if form.get('To')!=setting('TWILIO_PHONE_NUMBER'):
        raise HTTPException(403,'Unexpected receiving number.')
    address=phone(form.get('From'));body=str(form.get('Body',''))[:20000];sid=str(form.get('MessageSid',''))
    if not sid or db.scalar(select(Communication.id).where(Communication.provider_id==sid)):
        return Response('<Response/>',media_type='application/xml')
    # STOP is global, even if this sender cannot be attached unambiguously to a lead.
    if body.strip().upper() in ('STOP','STOPALL','UNSUBSCRIBE','CANCEL','END','QUIT','REVOKE','OPTOUT') or form.get('OptOutType')=='STOP':
        row=permission(db,'sms',address) or ContactPermission(channel='sms',address=address)
        row.status='blocked';row.evidence='Inbound provider STOP';row.updated_at=now();db.add(row)
    previous=db.scalar(select(Communication).where(Communication.channel=='sms',Communication.direction=='outbound',Communication.recipient==address).order_by(Communication.id.desc()).limit(1))
    if previous:
        db.add(Communication(actor_id=previous.actor_id,opportunity_id=previous.opportunity_id,command_id='inbound:'+sid,channel='sms',direction='inbound',recipient=address,body=body,subject='',status='received',provider_id=sid))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return Response('<Response/>',media_type='application/xml')


class CampaignIn(Input):
    name:str=Field(min_length=1,max_length=200)
    audience:Literal['subscribers','prospects']
    subject:str=Field(min_length=1,max_length=200,pattern=r'^[^\r\n]*$')
    body:str=Field(min_length=1,max_length=20000)
    opportunity_ids:list[int]=Field(min_length=1,max_length=50)


def campaign_access(db,user,id):
    _role(user,SALES)
    row=db.get(Campaign,id)
    if not row or row.actor_id!=user.id:
        raise HTTPException(404,'Campaign not found.')
    return row


def campaign_dict(row):
    return {key:getattr(row,key) for key in ('id','name','audience','subject','body','opportunity_ids','created_at')}


@router.get('/campaigns')
def campaigns(db:Session=Depends(get_db),user=Depends(current_user)):
    _role(user,SALES)
    return [campaign_dict(row) for row in db.scalars(select(Campaign).where(Campaign.actor_id==user.id).order_by(Campaign.id.desc()).limit(100))]


@router.post('/campaigns')
def create_campaign(data:CampaignIn,db:Session=Depends(get_db),user=Depends(current_user)):
    _role(user,SALES)
    for id in set(data.opportunity_ids):
        _edit_opportunity(db,user,id)
    row=Campaign(actor_id=user.id,**data.model_dump());db.add(row);db.commit();return campaign_dict(row)


def preview_campaign(db,user,row):
    results=[];seen=set()
    for id in dict.fromkeys(row.opportunity_ids):
        lead=_edit_opportunity(db,user,id)
        try:
            address=email(lead.email)
            if address in seen:
                results.append({'id':id,'name':lead.name,'email':address,'eligible':False,'reason':'Duplicate email'});continue
            seen.add(address)
            allowed(db,'email',address,optin=True)
            eligible=row.audience=='subscribers';reason='' if eligible else 'Prospect provider not connected'
        except HTTPException as e:
            address=lead.email;eligible=False;reason=e.detail
        receipt=db.scalar(select(Communication).where(Communication.actor_id==user.id,Communication.command_id==f'campaign:{row.id}:{id}'))
        results.append({'id':id,'name':lead.name,'email':address,'eligible':eligible and not receipt,'reason':receipt.status if receipt else reason})
    digest=hashlib.sha256(json.dumps({'campaign':campaign_dict(row),'recipients':results},sort_keys=True,default=str).encode()).hexdigest()
    return {'campaign':campaign_dict(row),'recipients':results,'review_token':digest}


@router.get('/campaigns/{id}/preview')
def campaign_preview(id:int,db:Session=Depends(get_db),user=Depends(current_user)):
    return preview_campaign(db,user,campaign_access(db,user,id))


def signer():
    require_settings('COMMUNICATIONS_SIGNING_KEY')
    return URLSafeSerializer(setting('COMMUNICATIONS_SIGNING_KEY'),salt='campaign-unsubscribe')


class CampaignSend(Input):
    review_token:str=Field(min_length=64,max_length=64)


@router.post('/campaigns/{id}/send')
def send_campaign(id:int,data:CampaignSend,db:Session=Depends(get_db),user=Depends(current_user)):
    row=campaign_access(db,user,id)
    require_settings('RESEND_API_KEY','RESEND_WEBHOOK_SECRET','CAMPAIGN_FROM_EMAIL','CAMPAIGN_POSTAL_ADDRESS','COMMUNICATIONS_SIGNING_KEY')
    preview=preview_campaign(db,user,row)
    if not secrets.compare_digest(preview['review_token'],data.review_token):
        raise HTTPException(409,'Recipients or permissions changed. Review this campaign again.')
    if row.audience!='subscribers':
        raise HTTPException(409,'Prospect campaigns need a separate approved provider.')
    eligible=[item for item in preview['recipients'] if item['eligible']]
    if not eligible:
        raise HTTPException(409,'No eligible recipients to send.')
    # Bound each explicit dispatch. Failed/unknown receipts block accidental retries.
    output=[]
    for item in eligible[:5]:
        lead=_edit_opportunity(db,user,item['id']);address=email(lead.email);allowed(db,'email',address,optin=True)
        unsubscribe=origin()+'/api/workspace/communications/unsubscribe/'+signer().dumps(address)
        body=row.body+'\n\n'+setting('CAMPAIGN_POSTAL_ADDRESS')+'\nUnsubscribe: '+unsubscribe
        receipt,fresh=reserve(db,user,lead,f'campaign:{id}:{lead.id}','campaign',address,row.subject,body)
        if not fresh:
            output.append(public_message(receipt));continue
        try:
            result=provider('POST','https://api.resend.com/emails',headers={'Authorization':'Bearer '+setting('RESEND_API_KEY'),'Idempotency-Key':f'mcc-campaign-{id}-{lead.id}'},json={'from':setting('CAMPAIGN_FROM_EMAIL'),'to':[address],'subject':row.subject,'text':body,'headers':{'List-Unsubscribe':'<'+unsubscribe+'>','List-Unsubscribe-Post':'List-Unsubscribe=One-Click'}})
            output.append(finish(db,receipt,'resend:'+result['id']))
        except HTTPException:
            receipt.status='unknown';db.commit();break
    return {'messages':output,'preview':preview_campaign(db,user,row)}


@router.get('/unsubscribe/{token}')
def unsubscribe_page(token:str):
    try:
        signer().loads(token)
    except BadSignature:
        raise HTTPException(400,'Invalid unsubscribe link.') from None
    # GET cannot opt someone out when a mail scanner previews a link.
    return HTMLResponse('<!doctype html><title>Email preferences</title><h1>Unsubscribe from Mill City campaigns</h1><form method="post"><button>Unsubscribe</button></form>',headers={'Referrer-Policy':'no-referrer','Cache-Control':'no-store'})


@router.post('/unsubscribe/{token}')
def unsubscribe(token:str,db:Session=Depends(get_db)):
    try:
        address=email(signer().loads(token))
    except BadSignature:
        raise HTTPException(400,'Invalid unsubscribe link.') from None
    row=permission(db,'email',address) or ContactPermission(channel='email',address=address)
    row.status='blocked';row.evidence='Recipient unsubscribe';row.updated_at=now();db.add(row);db.commit()
    return HTMLResponse('<!doctype html><title>Unsubscribed</title><h1>You are unsubscribed.</h1>',headers={'Referrer-Policy':'no-referrer','Cache-Control':'no-store'})


@router.post('/campaigns/events')
async def campaign_event(request:Request,db:Session=Depends(get_db)):
    require_settings('RESEND_WEBHOOK_SECRET')
    raw=await request.body()
    if len(raw)>262144:
        raise HTTPException(413,'Event too large.')
    try:
        event=Webhook(setting('RESEND_WEBHOOK_SECRET')).verify(raw,dict(request.headers))
    except (WebhookVerificationError,ValueError):
        raise HTTPException(403,'Invalid provider signature.') from None
    state={'email.delivered':'delivered','email.bounced':'bounced','email.complained':'complained','email.failed':'failed','email.suppressed':'suppressed'}.get(event.get('type'))
    data=event.get('data',{})
    if state:
        row=db.scalar(select(Communication).where(Communication.provider_id=='resend:'+str(data.get('email_id','')),Communication.channel=='campaign'))
        if row:
            if row.status not in ('bounced','complained','suppressed') or state in ('bounced','complained','suppressed'):
                row.status=state
            if state in ('bounced','complained','suppressed'):
                pref=permission(db,'email',row.recipient) or ContactPermission(channel='email',address=row.recipient)
                pref.status='blocked';pref.evidence='Provider '+state;pref.updated_at=now();db.add(pref)
            db.commit()
        else:
            # Ask the provider to retry when its event beats the send receipt commit.
            raise HTTPException(503,'Delivery receipt not yet available.')
    return {'received':True}
