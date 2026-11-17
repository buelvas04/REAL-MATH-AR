"""Estadísticas descriptivas pre/post sobre CSV REAL-MATH AR; no genera datos."""
from __future__ import annotations
import csv
import statistics
import sys
from pathlib import Path

def analizar(archivo: Path):
    if not archivo.is_file():
        raise SystemExit(f'No existe el archivo: {archivo}')
    by={}
    with archivo.open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            code=row['participante_anonimo']
            stage=row['etapa']
            if stage not in {'pre','post'}: continue
            by.setdefault(code,{})[stage]=int(row['puntaje'])
    paired=[v for v in by.values() if {'pre','post'}<=set(v)]
    print('Participantes con pretest y postest:',len(paired))
    print('Participantes sin pareja:',len(by)-len(paired))
    if not paired:
        print('No hay datos emparejados suficientes para analizar.')
        return
    pre=[v['pre'] for v in paired]
    post=[v['post'] for v in paired]
    diff=[b-a for a,b in zip(pre,post)]
    print('Promedio pretest:',round(statistics.mean(pre),2),'/6')
    print('Promedio postest:',round(statistics.mean(post),2),'/6')
    print('Cambio promedio:',round(statistics.mean(diff),2),'puntos')
    print('Mediana de cambio:',round(statistics.median(diff),2))
    print('Participantes con mejora:',sum(x>0 for x in diff),'/',len(diff))
    if len(diff)>1:
        print('Desviación estándar del cambio:',round(statistics.stdev(diff),3))
    print('\nEstas cifras son descriptivas. No prueban causalidad ni sustituyen un diseño válido, control de sesgos o evaluación estadística.')

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Uso: python analizar_resultados.py ruta/export.csv')
    analizar(Path(sys.argv[1]))
