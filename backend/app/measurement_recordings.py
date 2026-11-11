"""Private, immutable review audio attached to the notebook's job permissions."""
import hashlib
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import String, ForeignKey, LargeBinary, DateTime, select, func
from sqlalchemy.orm import Mapped, mapped_column, Session
from .db import Base,get_db
from .models import User,now
from .auth import current_user
from .resources import Notebook,note_access
router=APIRouter(prefix='/api/workspace/notebooks',tags=['review-recordings'])
class ReviewRecording(Base):
    __tablename__='workspace_review_recordings'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    notebook_id: Mapped[str]=mapped_column(ForeignKey('workspace_notebooks.id'),index=True)
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    title: Mapped[str]=mapped_column(String(200))
    content_type: Mapped[str]=mapped_column(String(80))
    content: Mapped[bytes]=mapped_column(LargeBinary)
    digest: Mapped[str]=mapped_column(String(64))
    size: Mapped[int]=mapped_column()
    duration: Mapped[int]=mapped_column()
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    consent_confirmed: Mapped[bool]=mapped_column(default=True)
def dto(r):return {k:getattr(r,k) for k in ('id','title','size','duration','content_type','created_at','user_id','consent_confirmed')}
@router.get('/{notebook_id}/recordings')
def recordings(notebook_id:UUID,user:User=Depends(current_user),db:Session=Depends(get_db)):
    note_access(db,user,db.get(Notebook,str(notebook_id)))
    return [dto(r) for r in db.scalars(select(ReviewRecording).where(ReviewRecording.notebook_id==str(notebook_id)).order_by(ReviewRecording.created_at))]
@router.put('/{notebook_id}/recordings/{id}')
async def upload(notebook_id:UUID,id:UUID,file:UploadFile=File(),title:str=Form('Customer review'),duration:int=Form(0),consent_confirmed:bool=Form(False),user:User=Depends(current_user),db:Session=Depends(get_db)):
    note_access(db,user,db.get(Notebook,str(notebook_id)),True)
    if not consent_confirmed:raise HTTPException(422,'Confirm everyone agreed to this recording.')
    if not title.strip() or len(title)>200 or duration<0 or duration>3600:raise HTTPException(422,'Invalid recording details.')
    data=await file.read(25_000_001)
    if not data or len(data)>25_000_000:raise HTTPException(422,'Recordings must be between 1 byte and 25 MB.')
    mime=(file.content_type or '').split(';')[0]
    signatures={'audio/mp4':len(data)>12 and data[4:8]==b'ftyp','audio/webm':data[:4]==b'\x1aE\xdf\xa3','audio/ogg':data[:4]==b'OggS'}
    if not signatures.get(mime):raise HTTPException(422,'Use an MP4, WebM, or Ogg audio recording.')
    # Serialize capacity checks on the notebook in PostgreSQL.
    note=db.scalar(select(Notebook).where(Notebook.id==str(notebook_id)).with_for_update());note_access(db,user,note,True)
    digest=hashlib.sha256(data).hexdigest();prior=db.get(ReviewRecording,str(id))
    if prior:
        if prior.notebook_id==str(notebook_id) and prior.user_id==user.id and prior.digest==digest:return dto(prior)
        raise HTTPException(409,'This recording ID already exists. The saved original has not changed.')
    total=db.scalar(select(func.sum(ReviewRecording.size)).where(ReviewRecording.notebook_id==str(notebook_id))) or 0
    if total+len(data)>100_000_000:raise HTTPException(422,'This notebook has reached 100 MB of audio. Download your pending recording and start another notebook.')
    row=ReviewRecording(id=str(id),notebook_id=str(notebook_id),user_id=user.id,title=title.strip(),duration=duration,content_type=mime,content=data,size=len(data),digest=digest,consent_confirmed=True)
    db.add(row);db.commit();return dto(row)
@router.get('/{notebook_id}/recordings/{id}/audio')
def audio(notebook_id:UUID,id:UUID,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    note_access(db,user,db.get(Notebook,str(notebook_id)))
    row=db.get(ReviewRecording,str(id))
    if not row or row.notebook_id!=str(notebook_id):raise HTTPException(404,'Recording unavailable.')
    headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Accept-Ranges':'bytes','Content-Disposition':'inline','Content-Security-Policy':"sandbox; default-src 'none'"}
    start,end=0,row.size-1;status=200
    value=request.headers.get('range')
    if value:
        import re
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
        if not match or not any(match.groups()):return Response(status_code=416,headers={'Content-Range':f'bytes */{row.size}'})
        a,b=match.groups()
        if a:start=int(a);end=min(int(b),end) if b else end
        else:start=max(0,row.size-int(b))
        if start>end or start>=row.size:return Response(status_code=416,headers={'Content-Range':f'bytes */{row.size}'})
        status=206;headers['Content-Range']=f'bytes {start}-{end}/{row.size}'
    headers['Content-Length']=str(end-start+1)
    return Response(row.content[start:end+1],media_type=row.content_type,status_code=status,headers=headers)
