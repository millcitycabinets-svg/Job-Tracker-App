from uuid import uuid4
from test_workspace import workspace,password_hash,ok,command
from test_resources import payload

def v2():return {'version':2,'pages':[{'id':'kitchen','scale':{'width_inches':144,'height_inches':96},'title':'Kitchen — sink wall','text':'96 inch ceiling','strokes':[{'color':'#1d4ed8','width':3,'points':[{'x':30,'y':50}]}],'bubbles':[{'id':'sink','x':10,'y':20,'color':'#dc2626','text':'Customer requested centered sink'}]},{'id':'island','scale':None,'title':'Island','text':'Seating side','strokes':[],'bubbles':[]}],'measurements':{'cabinet_selection':'White Shaker','service_package':'Customer pickup','color':'Custom oak','door_style':'Shaker','assembly':'Included','delivery':'Included','install':'To confirm','ceiling_height':'96 inches','upper_finish':'Crown molding','scope_notes':'Review after flooring'},'review_notes':'Customer asked for wider drawers'}
def test_multipage_roundtrip_and_old_client_cannot_erase_pages(workspace):
    c=workspace.client('rep_a');id=str(uuid4());body=payload(content=v2())
    saved=ok(c.put('/api/workspace/notebooks/'+id,json=body));read=ok(c.get('/api/workspace/notebooks/'+id))
    assert read['content']['pages']==body['content']['pages']
    assert read['content']['measurements']==body['content']['measurements']
    assert c.put('/api/workspace/notebooks/'+id,json=payload(revision=saved['revision'])).status_code==409
    body['revision']=saved['revision'];body['content']['pages'][1]['title']='Island — seating';ok(c.put('/api/workspace/notebooks/'+id,json=body))
    assert ok(c.get('/api/workspace/notebooks/'+id))['content']['pages'][0]['bubbles'][0]['text']=='Customer requested centered sink'
    body['content']['pages'][1]['id']='kitchen'
    assert c.put('/api/workspace/notebooks/'+id,json=body).status_code==422

def test_recordings_permissions_retry_immutable_original_and_audio_ranges(workspace):
    a=workspace.client('rep_a');b=workspace.client('rep_b');admin=workspace.client('admin');note=str(uuid4());record=str(uuid4())
    ok(a.put('/api/workspace/notebooks/'+note,json=payload(content=v2())))
    url=f'/api/workspace/notebooks/{note}/recordings/{record}'
    audio=b'\x00\x00\x00\x18ftypM4A '+b'0'*40
    upload={'files':{'file':('review.m4a',audio,'audio/mp4')},'data':{'title':'Customer final review','duration':'12','consent_confirmed':'true'}}
    assert b.put(url,**upload).status_code==404
    no_consent={**upload,'data':{**upload['data'],'consent_confirmed':'false'}}
    assert a.put(url,**no_consent).status_code==422
    ok(a.put(url,**upload));ok(a.put(url,**upload))
    rows=ok(a.get(f'/api/workspace/notebooks/{note}/recordings'));assert len(rows)==1 and rows[0]['consent_confirmed']
    assert b.get(url+'/audio').status_code==404
    full=a.get(url+'/audio');assert full.content==audio
    part=a.get(url+'/audio',headers={'Range':'bytes=4-11'});assert part.status_code==206 and part.content==audio[4:12] and part.headers['content-range']==f'bytes 4-11/{len(audio)}'
    assert a.get(url+'/audio',headers={'Range':'bytes=9999-'}).status_code==416
    assert a.get(url+'/audio',headers={'Range':'bytes=-5'}).content==audio[-5:]
    changed={**upload,'files':{'file':('review.m4a',audio+b'changed','audio/mp4')}}
    assert a.put(url,**changed).status_code==409
    bad={**upload,'files':{'file':('review.m4a',b'<html>not audio</html>','audio/mp4')}}
    assert a.put(url,**bad).status_code==422
    assert admin.get(url+'/audio').status_code==200
