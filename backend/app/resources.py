"""Authenticated team library and explicitly linked field notebooks."""
import json
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel, Field, ConfigDict, model_validator
from sqlalchemy import String, Text, JSON, LargeBinary, ForeignKey, DateTime, select, or_, func, update
from sqlalchemy.orm import Mapped, mapped_column, Session
from sqlalchemy.exc import IntegrityError
from .db import Base, get_db
from .models import User, Job, AuditLog, now
from .auth import current_user
from .workspace_models import Opportunity, WorkRequest
from .workspace import _role, WORKSPACE, _read_opportunity, _edit_opportunity

router=APIRouter(prefix='/api/workspace',tags=['resources'])

class TeamFile(Base):
    __tablename__='workspace_team_files'
    id: Mapped[int]=mapped_column(primary_key=True)
    path: Mapped[str]=mapped_column(String(500))
    size: Mapped[int]=mapped_column()
    content: Mapped[bytes]=mapped_column(LargeBinary)
    uploaded_by: Mapped[int]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Notebook(Base):
    __tablename__='workspace_notebooks'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    opportunity_id: Mapped[int | None]=mapped_column(ForeignKey('workspace_opportunities.id'),index=True)
    job_id: Mapped[int | None]=mapped_column(ForeignKey('jobs.id'),index=True)
    title: Mapped[str]=mapped_column(String(200))
    content: Mapped[dict]=mapped_column(JSON)
    revision: Mapped[int]=mapped_column(default=1)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Point(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    x: float=Field(ge=0,le=1200)
    y: float=Field(ge=0,le=900)
class Stroke(BaseModel):
    color: str=Field(pattern=r'^#[0-9a-fA-F]{6}$')
    width: float=Field(ge=1,le=12)
    points: list[Point]=Field(max_length=20000)
class TextBubble(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id: str=Field(min_length=1,max_length=80)
    x: float=Field(ge=0,le=1200)
    y: float=Field(ge=0,le=900)
    color: str=Field(pattern=r'^#[0-9a-fA-F]{6}$')
    text: str=Field(max_length=4000)
class MeasurementPage(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id: str=Field(min_length=1,max_length=80)
    title: str=Field(min_length=1,max_length=160)
    text: str=Field(default='',max_length=50000)
    strokes: list[Stroke]=Field(default_factory=list,max_length=2000)
    bubbles: list[TextBubble]=Field(default_factory=list,max_length=200)
class Measurements(BaseModel):
    model_config=ConfigDict(extra='forbid')
    cabinet_selection: str | None=Field(default=None,max_length=200)
    service_package: str=Field(default='',max_length=200)
    color: str=Field(default='',max_length=200)
    door_style: str=Field(default='',max_length=200)
    assembly: str=Field(default='To confirm',max_length=200)
    delivery: str=Field(default='To confirm',max_length=200)
    install: str=Field(default='To confirm',max_length=200)
    ceiling_height: str=Field(default='',max_length=200)
    upper_finish: str=Field(default='',max_length=200)
    scope_notes: str=Field(default='',max_length=10000)
class Content(BaseModel):
    model_config=ConfigDict(extra='forbid')
    # Legacy single-page payloads remain readable; new clients preserve them as page 1.
    text: str=Field(default='',max_length=50000)
    strokes: list[Stroke]=Field(default_factory=list,max_length=2000)
    version: int=Field(default=1,ge=1,le=2)
    pages: list[MeasurementPage]=Field(default_factory=list,max_length=30)
    measurements: Measurements=Field(default_factory=Measurements)
    review_notes: str=Field(default='',max_length=50000)
    @model_validator(mode='after')
    def unique_pages(self):
        if len({p.id for p in self.pages})!=len(self.pages):raise ValueError('Page IDs must be unique.')
        if self.version==2 and not self.pages:raise ValueError('A notebook needs at least one page.')
        for p in self.pages:
            if len({b.id for b in p.bubbles})!=len(p.bubbles):raise ValueError('Text note IDs must be unique.')
        return self
class NoteIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title: str=Field(min_length=1,max_length=200)
    opportunity_id: int | None=Field(default=None,gt=0)
    job_id: int | None=Field(default=None,gt=0)
    content: Content
    revision: int=Field(ge=0)

def link_access(db,user,opportunity_id=None,job_id=None,write=False):
    _role(user,WORKSPACE)
    if opportunity_id and job_id: raise HTTPException(422,'Choose one customer or job.')
    if opportunity_id:
        if user.role in ('owner','rep'):
            return (_edit_opportunity if write else _read_opportunity)(db,user,opportunity_id)
        q=select(WorkRequest).where(WorkRequest.opportunity_id==opportunity_id)
        if user.role=='designer': q=q.where(WorkRequest.assigned_to_id==user.id)
        if not db.scalar(q): raise HTTPException(404,'Customer work is unavailable.')
        return db.get(Opportunity,opportunity_id)
    if job_id:
        row=db.get(Job,job_id)
        if not row or (user.role=='rep' and row.rep_id!=user.id): raise HTTPException(404,'Job is unavailable.')
        if user.role=='designer':
            q=select(WorkRequest).join(Opportunity,Opportunity.id==WorkRequest.opportunity_id).where(Opportunity.linked_job_id==job_id,WorkRequest.assigned_to_id==user.id)
            if not db.scalar(q): raise HTTPException(404,'Job is unavailable.')
        return row

def note_access(db,user,row,write=False):
    _role(user,WORKSPACE)
    if not row: raise HTTPException(404,'Notebook is unavailable.')
    if row.opportunity_id or row.job_id: link_access(db,user,row.opportunity_id,row.job_id,write=False)
    elif row.user_id!=user.id and user.role!='owner': raise HTTPException(404,'Notebook is unavailable.')
    if write and row.user_id!=user.id and user.role!='owner': raise HTTPException(403,'Only the author or administrator can edit this notebook.')

def out(row,user,detail=False):
    value={'id':row.id,'title':row.title,'user_id':row.user_id,'opportunity_id':row.opportunity_id,'job_id':row.job_id,'revision':row.revision,'updated_at':row.updated_at,'editable':user.role=='owner' or row.user_id==user.id}
    if detail:value['content']=row.content
    return value

@router.get('/notebooks')
def notebooks(opportunity_id:int|None=None,job_id:int|None=None,db:Session=Depends(get_db),user:User=Depends(current_user)):
    _role(user,WORKSPACE)
    link_access(db,user,opportunity_id,job_id)
    q=select(Notebook)
    if opportunity_id:
        opp=db.get(Opportunity,opportunity_id)
        q=q.where(or_(Notebook.opportunity_id==opportunity_id,Notebook.job_id==opp.linked_job_id) if opp.linked_job_id else Notebook.opportunity_id==opportunity_id)
    elif job_id:
        ids=select(Opportunity.id).where(Opportunity.linked_job_id==job_id)
        q=q.where(or_(Notebook.job_id==job_id,Notebook.opportunity_id.in_(ids)))
    else:q=q.where(Notebook.user_id==user.id)
    result=[]
    for row in db.scalars(q.order_by(Notebook.updated_at.desc())):
        try:note_access(db,user,row)
        except HTTPException:continue
        result.append(out(row,user))
    return result

@router.get('/notebooks/{id}')
def notebook(id:UUID,db:Session=Depends(get_db),user:User=Depends(current_user)):
    row=db.get(Notebook,str(id));note_access(db,user,row)
    return out(row,user,True)

@router.put('/notebooks/{id}')
def save_notebook(id:UUID,body:NoteIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    _role(user,WORKSPACE)
    content=body.content.model_dump() if body.content.version==2 else {"text":body.content.text,"strokes":[s.model_dump() for s in body.content.strokes]}
    if len(json.dumps(content).encode())>12_000_000:raise HTTPException(422,'This notebook is full. Start another notebook for this job.')
    row=db.get(Notebook,str(id))
    if row:
        note_access(db,user,row,True)
        if row.content.get('version',1)==2 and content.get('version',1)<2:raise HTTPException(409,'This notebook has multiple pages. Refresh the app before editing; your draft is retained.')
        # Assignment is explicit and checked separately from editing an existing page.
        if (body.opportunity_id,body.job_id)!=(row.opportunity_id,row.job_id):link_access(db,user,body.opportunity_id,body.job_id,True)
        if row.revision!=body.revision:
            # Safely acknowledge an identical retry after a lost response.
            if row.title==body.title and row.content==content and (row.opportunity_id,row.job_id)==(body.opportunity_id,body.job_id): return out(row,user,True)
            raise HTTPException(409,'A newer version exists. Your device draft is retained. Export it before reopening the saved version.')
        result=db.execute(update(Notebook).where(Notebook.id==str(id),Notebook.revision==body.revision).values(title=body.title,content=content,opportunity_id=body.opportunity_id,job_id=body.job_id,revision=body.revision+1,updated_at=now()))
        if not result.rowcount:raise HTTPException(409,'This notebook changed on another device. Your local draft is retained.')
        db.add(AuditLog(user_id=user.id,table_name='workspace_notebooks',row_id=None,action='update',changes={'id':[str(id),str(id)],'revision':[body.revision,body.revision+1]}))
    else:
        if body.revision!=0:raise HTTPException(409,'Notebook is unavailable. Your device draft is retained.')
        link_access(db,user,body.opportunity_id,body.job_id,True)
        row=Notebook(id=str(id),user_id=user.id,title=body.title,content=content,opportunity_id=body.opportunity_id,job_id=body.job_id,revision=1)
        db.add(row)
    try:db.commit()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'Notebook changed. Retry saving your retained draft.') from None
    db.expire_all();return out(db.get(Notebook,str(id)),user,True)

@router.get('/team-files')
def team_files(db:Session=Depends(get_db),user:User=Depends(current_user)):
    _role(user,WORKSPACE)
    return [dict(row._mapping) for row in db.execute(select(TeamFile.id,TeamFile.path,TeamFile.size,TeamFile.created_at).order_by(TeamFile.path,TeamFile.id))]

@router.post('/team-files')
async def upload_file(file:UploadFile=File(),path:str=Form(''),db:Session=Depends(get_db),user:User=Depends(current_user)):
    _role(user,('owner',))
    name=(path or file.filename or 'file').replace('\\','/')
    parts=PurePosixPath(name).parts
    if len(name)>500 or name.startswith('/') or any(p in ('..','.') for p in parts) or any(ord(c)<32 for c in name):raise HTTPException(422,'Invalid folder or file name.')
    if PurePosixPath(name).suffix.lower() not in ('.pdf','.png','.jpg','.jpeg','.webp','.heic','.doc','.docx','.xls','.xlsx','.csv','.ppt','.pptx','.txt','.md','.zip'):
        raise HTTPException(422,'Use PDFs, images, Office documents, CSV, text, or ZIP files.')
    data=await file.read(25_000_001)
    if not data or len(data)>25_000_000:raise HTTPException(422,'Each file must be between 1 byte and 25 MB.')
    if (db.scalar(select(func.sum(TeamFile.size))) or 0)+len(data)>500_000_000:raise HTTPException(422,'The library has reached its 500 MB limit. Ask your administrator to expand storage.')
    row=TeamFile(path=name,size=len(data),content=data,uploaded_by=user.id);db.add(row);db.commit()
    return {'id':row.id,'path':row.path,'size':row.size}

@router.get('/team-files/{id}/download')
def download_file(id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    _role(user,WORKSPACE);row=db.get(TeamFile,id)
    if not row:raise HTTPException(404,'File not found.')
    return Response(row.content,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(PurePosixPath(row.path).name,safe=''),'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store','Content-Security-Policy':"sandbox; default-src 'none'"})
