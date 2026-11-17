"""Verifica rutas, rutas seguras, controles y recursos 3D del modo AR de portátil."""
from pathlib import Path
from fastapi.testclient import TestClient
import os, sys, tempfile
os.environ['DATABASE_PATH']=str(Path(tempfile.mkdtemp())/'ra_tests.db')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

def test_ar_webcam_pages_and_resources():
    c=TestClient(app.app)
    a=c.get('/ar?model=pitagoras')
    assert a.status_code==200
    assert 'a-marker' in a.text and 'preset="hiro"' in a.text
    assert 'arMathematicalModel' in a.text
    assert 'AR.js/3.4.8/' in a.text
    assert 'Object.hasOwn(descriptions,wanted)' in a.text
    m=c.get('/marcador')
    assert m.status_code==200 and 'hiro.png' in m.text
    for model in ('pitagoras','identidad_algebraica','calculo_-2','calculo_-1','calculo_0','calculo_1','calculo_2'):
        r=c.get('/assets/'+model+'.glb')
        assert r.status_code==200
        assert r.content[:4]==b'glTF'
    assert 'Activar RA con la cámara del portátil' in c.get('/assets/app.js').text
