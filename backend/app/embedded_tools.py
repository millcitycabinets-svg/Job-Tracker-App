"""Revocable, customer-scoped access to the existing design/estimate store.

Only a hash is persisted. The portal checks current membership on every request;
no shared administrator credential or browser-wide portal login is issued.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid5, NAMESPACE_URL
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import String, ForeignKey, DateTime, delete
from sqlalchemy.orm import Mapped, mapped_column, Session
from .db import Base, get_db
from .auth import current_user, session_fingerprint
from .models import User
from .resources import link_access

router=APIRouter(prefix='/api/workspace/tools',tags=['embedded-tools'])
PORTAL='https://dealers.millcitycabinetry.com'
class ToolGrant(Base):
    __tablename__='workspace_tool_grants'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    opportunity_id: Mapped[int]=mapped_column(ForeignKey('workspace_opportunities.id'))
    credential_version: Mapped[str]=mapped_column(String(64))
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
class Launch(BaseModel):
    opportunity_id: int=Field(gt=0)
class Token(BaseModel):
    token: str=Field(min_length=43,max_length=43,pattern=r'^[A-Za-z0-9_-]+$')
def digest(token):return hashlib.sha256(token.encode()).hexdigest()
def identifiers(opportunity_id):
    return {kind+'_id':str(uuid5(NAMESPACE_URL,f'https://jobs.millcitycabinetry.com/opportunities/{opportunity_id}/{kind}')) for kind in ('job','customer','tenant')}

@router.post('/launch')
def launch(body:Launch,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    link_access(db,user,body.opportunity_id,write=True)
    token=secrets.token_urlsafe(32)
    expires=datetime.now(timezone.utc)+timedelta(hours=8)
    db.execute(delete(ToolGrant).where(ToolGrant.expires_at<datetime.now(timezone.utc)))
    db.add(ToolGrant(id=digest(token),user_id=user.id,opportunity_id=body.opportunity_id,credential_version=session_fingerprint(user,request.app.state.session_secret),expires_at=expires));db.commit()
    return {'token':token,'url':PORTAL+'/workspace','expires_at':expires,**identifiers(body.opportunity_id)}

@router.post('/verify')
def verify(body:Token,request:Request,db:Session=Depends(get_db)):
    grant=db.get(ToolGrant,digest(body.token))
    if not grant or grant.expires_at.replace(tzinfo=timezone.utc)<=datetime.now(timezone.utc):raise HTTPException(401,'Reopen the tool from the customer file.')
    user=db.get(User,grant.user_id)
    if not user or not user.active or not hmac.compare_digest(grant.credential_version,session_fingerprint(user,request.app.state.session_secret)):raise HTTPException(401,'Workspace access has changed.')
    op=link_access(db,user,grant.opportunity_id,write=True)
    return {**identifiers(op.id),'opportunity_id':op.id,'user_id':user.id,'email':user.email,'name':op.name,'customer':op.customer,'address':op.address or '', 'customer_email':op.email or '', 'phone':op.phone or ''}

@router.post('/revoke')
def revoke(body:Token,user:User=Depends(current_user),db:Session=Depends(get_db)):
    db.execute(delete(ToolGrant).where(ToolGrant.id==digest(body.token),ToolGrant.user_id==user.id));db.commit()
    return {'ok':True}
