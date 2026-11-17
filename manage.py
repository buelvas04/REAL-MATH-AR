"""Comandos locales para habilitar profesores sin registro privilegiado abierto."""
import argparse
import getpass
import secrets
import sqlite3
from app import db, digest, now, pass_hash, valid_password, normalize_email

def main():
    parser=argparse.ArgumentParser(description='Administración local de REAL-MATH AR')
    sub=parser.add_subparsers(dest='command',required=True)
    inv=sub.add_parser('invite-teacher',help='Crear invitación docente de un solo uso')
    inv.add_argument('--hours',type=int,default=48)
    create=sub.add_parser('create-teacher',help='Crear una cuenta docente desde la consola del servidor')
    create.add_argument('--name',required=True)
    create.add_argument('--email',required=True)
    args=parser.parse_args()
    if args.command=='invite-teacher':
        if not 1<=args.hours<=720: parser.error('hours debe estar entre 1 y 720')
        token=secrets.token_urlsafe(30)
        with db() as con:con.execute('INSERT INTO teacher_invites(token_hash,expires_at) VALUES(?,?)',(digest(token),now()+args.hours*3600))
        print('INVITACIÓN DOCENTE DE UN SOLO USO (no compartir públicamente):')
        print(token)
        print(f'Vence en {args.hours} horas.')
    else:
        pwd=getpass.getpass('Contraseña segura (no se mostrará): ')
        valid_password(pwd)
        pwd2=getpass.getpass('Confirma contraseña: ')
        if pwd!=pwd2:parser.error('Las contraseñas no coinciden.')
        name=' '.join(args.name.split())
        email=normalize_email(args.email)
        if len(name)<2 or '@' not in email:parser.error('Nombre/correo inválidos.')
        try:
            with db() as con:con.execute("INSERT INTO users(name,email,password_hash,created_at,role,bio) VALUES(?,?,?,?, 'teacher','')",(name,email,pass_hash(pwd),now()))
        except sqlite3.IntegrityError:parser.error('Ya existe una cuenta con ese correo.')
        print('Cuenta de profesor creada. Ya puede iniciar sesión desde la web.')
if __name__=='__main__':main()
