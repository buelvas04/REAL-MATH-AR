"""Geometría generada matemáticamente; sin modelos descargados ni imágenes externas."""
import math
from pathlib import Path
import numpy as np
import trimesh

DEST=Path(__file__).resolve().parent/'static/assets'
DEST.mkdir(exist_ok=True,parents=True)

def box(extents, center, color):
    mesh=trimesh.creation.box(extents=extents)
    mesh.apply_translation(center)
    mesh.visual.vertex_colors=color
    return mesh

def tile(points, color, thick=.028):
    p=np.array(points,dtype=float)
    xy=np.array(p[:,:2]); z=np.mean(p[:,2])
    # Cuadrilátero con caras superior e inferior, triangulación exacta de dos triángulos.
    v=np.concatenate([np.column_stack([xy,np.full(4,z+thick/2)]),np.column_stack([xy,np.full(4,z-thick/2)])])
    f=np.array([[0,1,2],[0,2,3],[4,6,5],[4,7,6],[0,4,5],[0,5,1],[1,5,6],[1,6,2],[2,6,7],[2,7,3],[3,7,4],[3,4,0]])
    m=trimesh.Trimesh(vertices=v,faces=f,process=False)
    m.visual.vertex_colors=color
    return m

def line(start,end,color=(245,245,245,255),radius=.023):
    vec=np.array(end,dtype=float)-np.array(start,dtype=float)
    length=np.linalg.norm(vec)
    mesh=trimesh.creation.cylinder(radius=radius,height=length,sections=9)
    transform=trimesh.geometry.align_vectors([0,0,1],vec)
    if transform is not None:mesh.apply_transform(transform)
    mesh.apply_translation((np.array(start)+np.array(end))/2)
    mesh.visual.vertex_colors=color
    return mesh

def export(name,parts):
    scene=trimesh.Scene()
    for i,m in enumerate(parts):scene.add_geometry(m,geom_name=f'{name}_{i}')
    blob=scene.export(file_type='glb')
    (DEST/name).write_bytes(blob)
    assert trimesh.load(DEST/name,force='scene').geometry
    print(name,len(blob))

# RA1. 9+16 = 25 unidades cuadradas; el área del cuadrado de la hipotenusa es 25.
A=np.array([0.,0.,0.]);B=np.array([3.,0.,0.]);C=np.array([0.,4.,0.]);D=np.array([4.,3.,0.]);
def xy(*points):return [list(np.asarray(p)*.11+np.array([-.11,-.2,0])) for p in points]
a,b,c,d=xy(A,B,C,D)
parts=[tile(xy([0,0,0],[3,0,0],[3,-3,0],[0,-3,0]),(44,181,215,255)),
       tile(xy([-4,0,0],[0,0,0],[0,4,0],[-4,4,0]),(120,94,239,255)),
       tile(xy(B,B+D,C+D,C), (241,163,80,255)),
       tile(xy(A,B,C,C),(16,34,72,255),.055)]
for s,e in [(a,b),(a,c),(b,c)]:parts.append(line(np.array(s)+[0,0,.045],np.array(e)+[0,0,.045],radius=.014))
export('pitagoras.glb',parts)

# RA2. Prueba geométrica de (a+b)^2 = a² + ab + ab + b².
a,b=1.55,.95; scale=.42
rects=[(0,0,a,a,(120,94,239,255)),(a,0,a+b,a,(53,199,218,255)),
       (0,a,a,a+b,(53,199,218,255)),(a,a,a+b,a+b,(244,174,88,255))]
parts=[]
for x0,y0,x1,y1,col in rects:
    co=lambda x,y:[(x-(a+b)/2)*scale,(y-(a+b)/2)*scale,0]
    parts.append(tile([co(x0,y0),co(x1,y0),co(x1,y1),co(x0,y1)],col,.045))
for x in (0,a,a+b):parts.append(line([(x-(a+b)/2)*scale,-(a+b)/2*scale,.035],[(x-(a+b)/2)*scale,(a+b)/2*scale,.035],radius=.01))
for y in (0,a,a+b):parts.append(line([-(a+b)/2*scale,(y-(a+b)/2)*scale,.035],[(a+b)/2*scale,(y-(a+b)/2)*scale,.035],radius=.01))
export('identidad_algebraica.glb',parts)

# RA3. z = a(x²+y²), paraboloides (a=-2,-1,0,1,2).
def surface(k):
    N=42;x=np.linspace(-1,1,N);v=[];colors=[];f=[]
    for iy,y in enumerate(x):
        for ix,xx in enumerate(x):
            z=k*(xx*xx+y*y)*.25
            v.append([xx*.55,y*.55,z*.7]);colors.append([int(55+80*(ix/N)),int(170+50*(iy/N)),221,255])
    for iy in range(N-1):
        for ix in range(N-1):
            i=iy*N+ix; f.extend([[i,i+1,i+N],[i+1,i+N+1,i+N]])
    mesh=trimesh.Trimesh(vertices=v,faces=f,process=False)
    mesh.visual.vertex_colors=colors
    return mesh
for k in (-2,-1,0,1,2):
    parts=[surface(k)]
    parts.extend([line([-.65,0,0],[.65,0,0],(255,255,255,255),.009),line([0,-.65,0],[0,.65,0],(255,255,255,255),.009),line([0,0,-.5],[0,0,.5],(255,255,255,255),.009)])
    export(f'calculo_{k}.glb',parts)
