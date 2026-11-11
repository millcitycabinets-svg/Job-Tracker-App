"""Read-only website feed -> durable, claimable shared opportunities."""
import asyncio
import logging
import os
from uuid import UUID
import httpx
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import BigInteger, ForeignKey, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, SessionLocal
from .models import User
from .workspace_models import Opportunity, Activity

FEED_URL = 'https://millcitycabinetry.com/api/crm-feed'
log = logging.getLogger(__name__)

class WebsiteLeadReceipt(Base):
    __tablename__ = 'workspace_website_leads'
    source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_sequence: Mapped[int] = mapped_column(BigInteger, unique=True)
    source_created_at: Mapped[int] = mapped_column(BigInteger, index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey('workspace_opportunities.id'), unique=True)

class WebsiteContact(BaseModel):
    model_config = ConfigDict(extra='ignore')
    id: UUID
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254, pattern=r'^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$')
    phone: str = Field(default='', max_length=30)
    location: str = Field(max_length=160)
    projectType: str = Field(max_length=80)
    description: str = Field(max_length=4000)

class WebsiteItem(BaseModel):
    sequence: int = Field(ge=1)
    id: UUID
    created_at: int = Field(ge=1)
    data: WebsiteContact


def ingest(items, sessions=SessionLocal):
    # Validate the whole page before advancing the durable cursor.
    parsed=[WebsiteItem.model_validate(item) for item in items]
    if any(item.id != item.data.id for item in parsed):
        raise ValueError('Website identity mismatch')
    imported=0
    for item in sorted(parsed,key=lambda x:x.sequence):
        with sessions() as db:
            source_id=str(item.id)
            if db.get(WebsiteLeadReceipt,source_id):
                continue  # Never reassign or overwrite a lead that has been claimed.
            owner=db.scalar(select(User).where(User.role=='owner',User.active.is_(True)).order_by(User.id))
            if not owner:
                raise ValueError('An active administrator is required')
            data=item.data
            row=Opportunity(name=f'{data.name} — {data.projectType}',customer=data.name,email=data.email.lower(),phone=data.phone or None,address=data.location,owner_id=owner.id,visibility='shared',stage='New Lead',work_scope=data.description,notes=f'Website consultation · https://millcitycabinetry.com\nReference: {source_id}\nProject: {data.projectType}\n\n{data.description}\n\nThis inquiry requests project follow-up; it does not grant campaign or SMS consent.')
            try:
                db.add(row);db.flush()
                db.add(WebsiteLeadReceipt(source_id=source_id,source_sequence=item.sequence,source_created_at=item.created_at,opportunity_id=row.id))
                db.add(Activity(opportunity_id=row.id,actor_id=owner.id,kind='Note',body='Website consultation received automatically in the shared lead pool. Claim this lead before following up.'))
                db.commit();imported+=1
            except IntegrityError:
                db.rollback()
                if not db.get(WebsiteLeadReceipt,source_id):
                    raise
    return imported


def cursor(sessions=SessionLocal):
    with sessions() as db:
        row=db.scalar(select(WebsiteLeadReceipt).order_by(WebsiteLeadReceipt.source_sequence.desc()).limit(1))
        return {'after':row.source_sequence} if row else {'after':0}

async def sync_once():
    token=os.environ.get('WEBSITE_FEED_TOKEN','')
    if not token or os.environ.get('WORKSPACE_ENVIRONMENT')=='local-pilot':
        return 0
    total=0
    async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
        for _ in range(20):
            params=await asyncio.to_thread(cursor)
            response=await client.get(FEED_URL,params=params,headers={'Authorization':'Bearer '+token})
            response.raise_for_status()
            items=response.json()['items']
            if not isinstance(items,list) or len(items)>100:
                raise ValueError('Invalid website page')
            if not items:break
            total+=await asyncio.to_thread(ingest,items)
            if len(items)<100:break
    return total

async def run():
    while True:
        try:
            count=await sync_once()
            if count:log.info('Imported %s website consultations into shared leads',count)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not log contact payloads, credentials, or authenticated requests.
            log.warning('Website lead sync could not complete; retrying in 60 seconds')
        await asyncio.sleep(60)
