import os
from pathlib import Path
import sys
import tempfile

# Tests corren en BD aislada.
os.environ['DATABASE_PATH']=str(Path(tempfile.mkdtemp())/'test.db')
os.environ['APP_ENV']='development'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import app as mod

client=TestClient(mod.app)

def test_end_to_end_auth_progress_recovery():
    email='ejemplo@universidad.edu.co'
    bad=client.post('/api/register',json={'name':'Aprendiz','email':email,'password':'123'})
    assert bad.status_code==422
    r=client.post('/api/register',json={'name':'Aprendiz Test','email':email,'password':'ClaveLarga2026'})
    assert r.status_code==201, r.text
    csrf=r.json()['csrf']
    assert 'httponly' in r.headers['set-cookie'].lower()
    assert client.get('/api/me').status_code==200
    assert 'password_hash' not in client.get('/api/me').text
    assert client.post('/api/answers',json={'resource':'geo','question':0,'choice':1}).status_code==403
    r=client.post('/api/answers',json={'resource':'geo','question':0,'choice':1},headers={'X-CSRF-Token':csrf})
    assert r.status_code==200 and r.json()['correct']
    assert client.get('/api/progress').json()['correct']==1
    res=client.post('/api/study/submit',headers={'X-CSRF-Token':csrf},json={'stage':'post','answers':[1,2,0,1,2,1],'consent':True})
    assert res.status_code==409
    res=client.post('/api/study/submit',headers={'X-CSRF-Token':csrf},json={'stage':'pre','answers':[1,2,0,1,2,1],'consent':True})
    assert res.status_code==200 and res.json()['score']==6
    assert client.post('/api/study/submit',headers={'X-CSRF-Token':csrf},json={'stage':'pre','answers':[1,2,0,1,2,1],'consent':True}).status_code==409
    assert client.post('/api/study/submit',headers={'X-CSRF-Token':csrf},json={'stage':'post','answers':[1,0,0,0,2,1],'consent':True}).status_code==200
    assert client.post('/api/study/perception',headers={'X-CSRF-Token':csrf},json={'scores':[3,4,5,2]}).status_code==200
    assert client.post('/api/progress/reset',headers={'X-CSRF-Token':csrf}).status_code==200
    assert client.get('/api/progress').json()['answered']==0
    assert client.post('/api/logout',headers={'X-CSRF-Token':csrf}).status_code==200
    assert client.get('/api/me').status_code==401
    assert client.post('/api/login',json={'email':email,'password':'wrong'}).status_code==401
    assert client.post('/api/forgot',json={'email':'nadie@ejemplo.com'}).status_code==200
    assert client.post('/api/forgot',json={'email':email}).status_code==200
    import re
    log=(mod.ROOT/'data'/'dev_mail.log').read_text(encoding='utf8')
    token=re.findall(r'\?reset_token=([\w-]+)',log)[-1]
    assert client.post('/api/reset-password',json={'token':token,'password':'NuevaClave2026'}).status_code==200
    assert client.post('/api/reset-password',json={'token':token,'password':'OtraClave2026'}).status_code==400
    assert client.post('/api/login',json={'email':email,'password':'ClaveLarga2026'}).status_code==401
    assert client.post('/api/login',json={'email':email,'password':'NuevaClave2026'}).status_code==200
