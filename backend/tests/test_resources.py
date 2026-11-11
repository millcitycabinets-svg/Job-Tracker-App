from uuid import uuid4
from test_workspace import workspace,password_hash,ok,command


def payload(**kw):
    return {'title':'Kitchen measurements','opportunity_id':None,'job_id':None,'revision':0,'content':{'text':'Ceiling 96 inches','strokes':[{'color':'#123456','width':3,'points':[{'x':20,'y':30},{'x':70,'y':80}]}]},**kw}

def test_notebook_private_assign_save_retry_and_conflict(workspace):
    a=workspace.client('rep_a'); b=workspace.client('rep_b');id=str(uuid4()); body=payload()
    saved=ok(a.put('/api/workspace/notebooks/'+id,json=body))
    assert saved['revision']==1
    assert b.get('/api/workspace/notebooks/'+id).status_code==404
    assert ok(a.put('/api/workspace/notebooks/'+id,json=body))['revision']==1
    assert a.put('/api/workspace/notebooks/'+id,json=payload(title='Stale overwrite')).status_code==409
    assert ok(a.get('/api/workspace/notebooks/'+id))['content']==body['content']
    body['revision']=1;body['job_id']=workspace.jobs['a_open']
    saved=ok(a.put('/api/workspace/notebooks/'+id,json=body))
    assert len(ok(a.get('/api/workspace/notebooks',params={'job_id':body['job_id']})))==1
    body['revision']=2;body['job_id']=workspace.jobs['b_closed']
    assert a.put('/api/workspace/notebooks/'+id,json=body).status_code==404
    assert workspace.client('crew').get('/api/workspace/notebooks').status_code==403

def test_assigned_designer_and_ops_see_handed_over_notes(workspace):
    a=workspace.client('rep_a');admin=workspace.client('admin');designer=workspace.client('designer');ops=workspace.client('ops')
    opp=ok(a.post('/api/workspace/opportunities',json={'name':'Kitchen job','customer':'Test customer','stage':'New Lead'}))
    id=str(uuid4());body=payload(opportunity_id=opp['id']);ok(a.put('/api/workspace/notebooks/'+id,json=body))
    assert designer.get('/api/workspace/notebooks/'+id).status_code==404
    assert ops.get('/api/workspace/notebooks/'+id).status_code==404
    opp=ok(a.patch(f"/api/workspace/opportunities/{opp['id']}",json=command(opp['revision'],stage='Design')))
    requests=ok(admin.get('/api/workspace/requests'));req=next(r for r in requests if r['opportunity_id']==opp['id'])
    ok(admin.patch(f"/api/workspace/requests/{req['id']}",json=command(req['revision'],assigned_to_id=workspace.user_ids['designer'])))
    assert ok(designer.get('/api/workspace/notebooks/'+id))['editable'] is False
    assert ok(ops.get('/api/workspace/notebooks/'+id))['title']==body['title']
    assert designer.put('/api/workspace/notebooks/'+id,json={**body,'revision':1}).status_code==403

def test_library_upload_roles_paths_download(workspace):
    admin=workspace.client('admin');rep=workspace.client('rep_a')
    args={'files':{'file':('guide.txt',b'Team guide','text/plain')},'data':{'path':'Guides/Measuring/guide.txt'}}
    assert rep.post('/api/workspace/team-files',**args).status_code==403
    row=ok(admin.post('/api/workspace/team-files',**args))
    assert ok(rep.get('/api/workspace/team-files'))[0]['path']=='Guides/Measuring/guide.txt'
    response=rep.get(f"/api/workspace/team-files/{row['id']}/download")
    assert response.content==b'Team guide' and response.headers['content-disposition'].startswith('attachment;')
    args['data']['path']='../escape.txt'
    assert admin.post('/api/workspace/team-files',**args).status_code==422
    args['data']['path']='Guide/unsafe.html'
    assert admin.post('/api/workspace/team-files',**args).status_code==422


def test_uuid_notebook_audit_uses_nullable_numeric_row_id(workspace):
    from sqlalchemy import select
    from app.models import AuditLog
    a=workspace.client('rep_a');id=str(uuid4())
    ok(a.put('/api/workspace/notebooks/'+id,json=payload()))
    ok(a.put('/api/workspace/notebooks/'+id,json=payload(title='Revision two',revision=1)))
    with workspace.session() as db:
        entries=list(db.scalars(select(AuditLog).where(AuditLog.table_name=='workspace_notebooks').order_by(AuditLog.id)))
        assert [e.action for e in entries]==['create','update']
        assert all(e.row_id is None for e in entries)
        assert entries[0].changes['id'][1]==id
        assert entries[1].changes['revision']==[1,2]
