from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from test_workspace import workspace,password_hash,ok,command
from app.embedded_tools import ToolGrant
from app.models import User

def create(client):return ok(client.post('/api/workspace/opportunities',json={'name':'Kitchen test','customer':'Bridge customer','email':'customer@example.com','stage':'New Lead'}))
def launch(client,op):return client.post('/api/workspace/tools/launch',json={'opportunity_id':op['id']})
def verify(client,token):return client.post('/api/workspace/tools/verify',json={'token':token})

def test_link_stable_scoped_revocable_and_no_duplicate_jobs(workspace):
    a=workspace.client('rep_a');b=workspace.client('rep_b');op=create(a)
    first=ok(launch(a,op));second=ok(launch(a,op))
    assert first['job_id']==second['job_id'] and first['token']!=second['token']
    assert launch(b,op).status_code==404
    claim=ok(verify(b,first['token'])) # portal verifies a capability, not its own CRM cookie
    assert claim['job_id']==first['job_id'] and claim['customer']=='Bridge customer'
    assert 'password' not in str(claim)
    assert verify(a,'x'*43).status_code==401
    ok(a.post('/api/workspace/tools/revoke',json={'token':first['token']}))
    assert verify(a,first['token']).status_code==401
    assert verify(a,second['token']).status_code==200
    ok(a.post('/api/logout'))
    assert verify(b,second['token']).status_code==401

def test_grants_recheck_assignment_inactive_users_password_and_expiry(workspace):
    a=workspace.client('rep_a');admin=workspace.client('admin');op=create(a)
    token=ok(launch(a,op))['token']
    with workspace.session() as db:
        user=db.get(User,workspace.user_ids['rep_a']);user.active=False;db.commit()
    assert verify(admin,token).status_code==401
    with workspace.session() as db:
        user=db.get(User,workspace.user_ids['rep_a']);user.active=True
        row=db.scalar(select(ToolGrant));row.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);db.commit()
    assert verify(admin,token).status_code==401

def test_shared_leads_must_be_claimed_and_designer_assigned(workspace):
    a=workspace.client('rep_a');admin=workspace.client('admin');designer=workspace.client('designer')
    op=create(a)
    assert launch(designer,op).status_code==404
    op=ok(a.patch(f"/api/workspace/opportunities/{op['id']}",json=command(op['revision'],stage='Design')))
    req=next(r for r in ok(admin.get('/api/workspace/requests')) if r['opportunity_id']==op['id'])
    ok(admin.patch(f"/api/workspace/requests/{req['id']}",json=command(req['revision'],assigned_to_id=workspace.user_ids['designer'])))
    token=ok(launch(designer,op))['token']
    assert verify(admin,token).status_code==200
    req=next(r for r in ok(admin.get('/api/workspace/requests')) if r['opportunity_id']==op['id'])
    ok(admin.patch(f"/api/workspace/requests/{req['id']}",json=command(req['revision'],assigned_to_id=None)))
    assert verify(admin,token).status_code==404
