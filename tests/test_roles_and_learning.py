"""Pruebas automatizadas de roles, permisos, cursos, progreso y configuración."""
import os
import sys
import tempfile
from pathlib import Path
os.environ['DATABASE_PATH']=str(Path(tempfile.mkdtemp())/'v3_test.db')
os.environ['APP_ENV']='development'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import app as m

def test_roles_course_quiz_profile_and_security():
    student=TestClient(m.app)
    teacher=TestClient(m.app)
    intruder=TestClient(m.app)
    teacher_email='matematicas@universidad.edu.co'
    student_email='alumno@universidad.edu.co'
    # No can assume teacher role from open registration.
    r=student.post('/api/register',json={'name':'Estudiante Real','email':student_email,'password':'Estudiante2026Segura'})
    assert r.status_code==201,r.text
    st_csrf=r.json()['csrf'];assert r.json()['role']=='student'
    headers_s={'X-CSRF-Token':st_csrf}
    assert 'role' in student.get('/api/me').json()
    assert student.post('/api/teacher/courses',json={},headers=headers_s).status_code==422
    assert student.post('/api/teacher/courses',json={'title':'Curso no permitido','subject':'geometria','description':'','published':True,'private':False},headers=headers_s).status_code==403
    # Teacher invite exists only in server DB; once used cannot be replayed.
    token='SecureTeacherInvite0123456789'
    with m.db() as conn:conn.execute('INSERT INTO teacher_invites VALUES(?,?,NULL)',(m.digest(token),m.now()+10000))
    r=teacher.post('/api/register-teacher',json={'name':'Profesora Real','email':teacher_email,'password':'Profesora2026Segura','invitation':token})
    assert r.status_code==201,r.text
    assert r.json()['role']=='teacher'
    h={'X-CSRF-Token':r.json()['csrf']}
    assert teacher.post('/api/register-teacher',json={'name':'Otro Profesor','email':'otro@universidad.co','password':'Profesor2026Seguro','invitation':token}).status_code==400
    assert teacher.get('/api/student/overview').status_code==403
    assert teacher.get('/api/study/status').status_code==403
    # Public curated sample courses are already available and safe for student to enroll in.
    public=student.get('/api/catalog').json()
    assert len(public)>=3
    assert all('access_code' not in item for item in public)
    sample=next(x for x in public if x['subject']=='geometria')
    assert student.get(f'/api/courses/{sample["id"]}').status_code==403
    assert student.post(f'/api/courses/{sample["id"]}/enroll',headers=headers_s).status_code==200
    sample_details=student.get(f'/api/courses/{sample["id"]}').json()
    assert len(sample_details['lessons'])>=4 and len(sample_details['quizzes'])>=2
    first_lesson=sample_details['lessons'][0]['id']
    assert student.post(f'/api/lessons/{first_lesson}/complete',headers=headers_s).status_code==200
    assert student.get('/api/student/overview').json()['lessons_completed']==1
    # Professor course: private by code, separate ownership.
    payload={'title':'Cátedra de álgebra lineal','subject':'algebra','description':'Transformaciones y pruebas','published':True,'private':True}
    cr=teacher.post('/api/teacher/courses',headers=h,json=payload)
    assert cr.status_code==201,cr.text
    cid=cr.json()['id'];code=cr.json()['access_code'];assert len(code)>=20
    assert all(x['id']!=cid for x in student.get('/api/catalog').json())
    assert student.post('/api/enroll',headers=headers_s,json={'code':'FAIL'}).status_code==404
    r=student.post('/api/enroll',headers=headers_s,json={'code':code})
    assert r.status_code==200,r.text
    assert student.get(f'/api/courses/{cid}').status_code==200
    assert 'access_code' not in student.get(f'/api/courses/{cid}').json()
    ldata={'title':'Álgebra paso a paso','body':'Primero hay que comprender las hipótesis del teorema y luego justificar cada paso.',
           'ar_model':'identidad_algebraica','support_url':'https://example.org/material','position':1,'published':True}
    assert teacher.post(f'/api/teacher/courses/{cid}/lessons',json={**ldata,'ar_model':'https://evil.example'},headers=h).status_code==422
    ls=teacher.post(f'/api/teacher/courses/{cid}/lessons',json=ldata,headers=h)
    assert ls.status_code==201,ls.text
    lid=ls.json()['id']
    assert student.post(f'/api/lessons/{lid}/complete',headers=headers_s).status_code==200
    # Quiz does not reveal correct answer to student.
    qdata={'title':'Examen inicial de álgebra','instructions':'Resuelve con cuidado','kind':'exam','time_limit':2,'max_attempts':2,'published':True}
    q=teacher.post(f'/api/teacher/courses/{cid}/quizzes',json=qdata,headers=h)
    assert q.status_code==201,q.text
    qid=q.json()['id']
    qp={'prompt':'Cuánto vale (a+b)²?','options':['a²+b²','a²+2ab+b²','a²-b²'], 'answer_index':1,'explanation':'Los dos productos mixtos suman 2ab.','position':1}
    qres=teacher.post(f'/api/teacher/quizzes/{qid}/questions',json=qp,headers=h)
    assert qres.status_code==201,qres.text
    assert student.get(f'/api/teacher/quizzes/{qid}').status_code==403
    before=student.post(f'/api/quizzes/{qid}/start',headers=headers_s)
    assert before.status_code==200,before.text
    assert 'answer_index' not in str(before.json()) and 'explanation' not in str(before.json())
    question_id=before.json()['questions'][0]['id'];aid=before.json()['attempt_id']
    assert student.post(f'/api/attempts/{aid}/submit',json={'choices':{str(question_id):1}}).status_code==403
    result=student.post(f'/api/attempts/{aid}/submit',headers=headers_s,json={'choices':{str(question_id):1}})
    assert result.status_code==200,result.text
    assert result.json()['score']==1 and result.json()['total']==1
    assert student.post(f'/api/attempts/{aid}/submit',headers=headers_s,json={'choices':{str(question_id):1}}).status_code==409
    overview=student.get('/api/student/overview').json();assert overview['exams_taken']==1
    report=teacher.get(f'/api/teacher/courses/{cid}/report').json()
    assert len(report['students'])==1 and report['results'][0]['score']==1
    # Another teacher cannot edit or view the first teacher's course.
    tok='AnotherTeacherOneTimeInvite00000'
    with m.db() as conn:conn.execute('INSERT INTO teacher_invites VALUES(?,?,NULL)',(m.digest(tok),m.now()+10000))
    r=intruder.post('/api/register-teacher',json={'name':'Profesor Extra','email':'extra@universidad.edu.co','password':'DocenteExtra2026','invitation':tok})
    assert r.status_code==201
    h2={'X-CSRF-Token':r.json()['csrf']}
    assert intruder.get(f'/api/courses/{cid}').status_code==403
    assert intruder.get(f'/api/teacher/courses/{cid}/report').status_code==404
    assert intruder.delete(f'/api/teacher/courses/{cid}',headers=h2).status_code==404
    # Change profile and password revoke current session.
    assert student.patch('/api/profile',headers=headers_s,json={'name':'Alumno Actualizado','bio':'Matemáticas 2026'}).status_code==200
    assert student.get('/api/me').json()['name']=='Alumno Actualizado'
    assert student.post('/api/profile/password',headers=headers_s,json={'current_password':'mal','new_password':'NuevaSegura2026'}).status_code==401
    assert student.post('/api/profile/password',headers=headers_s,json={'current_password':'Estudiante2026Segura','new_password':'NuevaSegura2026'}).status_code==200
    assert student.get('/api/me').status_code==401
    r=student.post('/api/login',json={'email':student_email,'password':'NuevaSegura2026'})
    assert r.status_code==200
    hs={'X-CSRF-Token':r.json()['csrf']}
    assert student.request('DELETE','/api/profile',headers=hs,json={'password':'NuevaSegura2026','confirmation':'ELIMINAR'}).status_code==200
    assert student.get('/api/me').status_code==401
    report=teacher.get(f'/api/teacher/courses/{cid}/report').json()
    assert len(report['students'])==0
