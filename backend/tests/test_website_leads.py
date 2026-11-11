from uuid import uuid4
from sqlalchemy import select,func
from app import website_leads as w
from app.workspace_models import Opportunity,WorkRequest
from app.communication_models import ContactPermission
from test_workspace import workspace,password_hash,ok,command

def item(sequence=1):
    id=str(uuid4())
    return {'sequence':sequence,'id':id,'created_at':1790000000000,'data':{'id':id,'name':'Website visitor','email':'visitor@example.test','phone':'6025550101','location':'Queen Creek','projectType':'Kitchen cabinetry','description':'Please help plan my kitchen.'}}

def test_website_request_is_shared_claimable_and_replay_preserves_owner(workspace):
    data=item()
    assert w.ingest([data],workspace.session)==1
    assert w.ingest([data],workspace.session)==0
    assert w.cursor(workspace.session)=={'after':1}
    with workspace.session() as db:
        row=db.scalar(select(Opportunity));id=row.id;revision=row.revision
        assert row.visibility=='shared' and row.stage=='New Lead'
        assert row.email=='visitor@example.test' and row.address=='Queen Creek'
        assert db.scalar(select(func.count()).select_from(WorkRequest))==0
        assert db.scalar(select(func.count()).select_from(ContactPermission))==0
    assert ok(workspace.client('admin').get('/api/workspace/opportunities?scope=mine'))==[]
    client=workspace.client('rep_a')
    assert len(ok(client.get('/api/workspace/opportunities?scope=shared')))==1
    ok(client.post(f'/api/workspace/opportunities/{id}/claim',json=command(revision)))
    assert w.ingest([data],workspace.session)==0
    assert len(ok(client.get('/api/workspace/opportunities?scope=mine')))==1
    assert ok(workspace.client('rep_b').get('/api/workspace/opportunities?scope=shared'))==[]
    with workspace.session() as db:
        assert db.get(Opportunity,id).owner_id==workspace.user_ids['rep_a']

def test_invalid_page_does_not_partially_advance(workspace):
    import pytest
    valid=item();invalid=item(2);invalid['data']['id']=str(uuid4())
    with pytest.raises(ValueError):w.ingest([valid,invalid],workspace.session)
    assert w.cursor(workspace.session)=={'after':0}

def test_website_alerts_only_sales_and_unclaimed_website_inquiries(workspace):
    data=item()
    w.ingest([data],workspace.session)
    a=workspace.client('rep_a'); b=workspace.client('rep_b')
    alerts=ok(a.get('/api/workspace/website-lead-alerts'))
    assert alerts['count']==1 and len(alerts['items'])==1
    assert ok(b.get('/api/workspace/website-lead-alerts'))==alerts
    assert workspace.client('designer').get('/api/workspace/website-lead-alerts').status_code==403
    id=alerts['items'][0]['id']
    row=ok(a.get(f'/api/workspace/opportunities/{id}'))
    ok(a.post(f'/api/workspace/opportunities/{id}/claim',json=command(row['revision'])))
    assert ok(b.get('/api/workspace/website-lead-alerts'))=={'count':0,'items':[]}
