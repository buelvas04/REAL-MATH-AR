# REAL-MATH AR — aplicación de Geometría, Álgebra y Cálculo

**Versión 3 · Profesor + estudiante · Proyecto de investigación universitario**

Esta versión parte del HTML enviado y del proyecto académico: diagnóstico de las dificultades (objetivo 1), diseño pedagógico de 3 recursos (objetivo 2), implementación del prototipo (objetivo 3) e instrumentos piloto para su evaluación (objetivo 4).

## 1. Abrir el proyecto en Windows

**Necesitas Python 3.11 o superior**, una conexión a Internet para instalar las dependencias y un navegador actualizado.

**Opción sencilla:** extrae todo el ZIP, entra a la carpeta `REAL_MATH_AR_ROLES_V3` y haz doble clic en `INICIAR_WINDOWS.bat`.

**Opción manual en PowerShell:**

```powershell
cd RUTA\REAL_MATH_AR_ROLES_V3
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Abre **http://127.0.0.1:8000** en el navegador. No abras `static/index.html` con doble clic: el inicio de sesión y la base de datos requieren que el servidor Python esté iniciado.

En macOS/Linux, usa `python3 -m venv .venv`, `source .venv/bin/activate` y ejecuta el mismo `pip install` y `python -m uvicorn`.

## 2. Crear las dos clases de cuenta

### A. Estudiante

En la pantalla de inicio elige **Registrarme**. Los alumnos se registran con nombre, correo y contraseña de al menos 10 caracteres, con letras y números. Si ya están registrados, entran en **Ingresar**. La cuenta siempre tiene el rol `student`, que no puede crear ni modificar materias.

### B. Profesor

**Un estudiante nunca puede autoasignarse el rol de profesor**. El administrador local de la aplicación tiene que emitir una invitación de un solo uso:

```powershell
python manage.py invite-teacher --hours 48
```

Este comando muestra un código temporal. En el inicio de la página, pulsa **Soy profesor**, completa el registro y pega ese código. No compartas invitaciones por redes sociales públicas.

También puedes crear directamente una cuenta docente desde la terminal del servidor:

```powershell
python manage.py create-teacher --name "Profesora de Matemáticas" --email "docente@universidad.edu.co"
```

La terminal pedirá la contraseña sin mostrarla. **No hay contraseñas ni cuentas de profesor predeterminadas.**

## 3. Qué puede hacer cada rol

| Función | Estudiante | Profesor |
| --- | --- | --- |
| Crear cuenta/iniciar sesión | Sí | Sí, con invitación |
| Editar perfil/correo/contraseña | Sí | Sí |
| Recuperar contraseña | Sí | Sí |
| Eliminar cuenta | Sí | Sí |
| Ver y entrar en cursos publicados | Sí | Solo sus cursos como editor |
| Inscribirse en un curso público | Sí | No |
| Inscribirse mediante código de curso privado | Sí | No |
| Crear, editar, publicar y borrar cursos | No | Sí, solo propios |
| Añadir lecciones y modelos RA seleccionables | No | Sí |
| Crear exámenes, preguntas y respuestas | No | Sí |
| Resolver exámenes y recibir retroalimentación | Sí | No |
| Ver progreso personal | Sí | No |
| Consultar calificaciones de sus alumnos | No | Sí, solo alumnos de sus cursos |
| Pretest/postest piloto de investigación | Sí | No |

El profesor elige área, curso público o privado, títulos y contenido. Los **códigos de curso privado** son distintos de las invitaciones para crear profesores.

## 4. Biblioteca pedagógica preinstalada

El primer arranque de una base de datos nueva incorpora **3 cursos públicos, 12 lecciones, 3 cuestionarios de práctica y 3 exámenes de repaso**:

- **Geometría:** Teorema de Pitágoras, demostración por áreas, semejanza de triángulos y vectores.
- **Álgebra:** identidad `(a+b)²`, diferencia de cuadrados, ecuaciones lineales y transformaciones lineales.
- **Cálculo:** parábolas, derivadas, superficies 3D y gradiente.

Estos contenidos se entregan como **material inicial para repasar y probar la plataforma**, no como resultado validado de la investigación. Los profesores pueden añadir asignaturas y clases nuevas. Las preguntas tienen opciones, clave correcta, explicación, tiempo de examen y número máximo de intentos.

## 5. Realidad Aumentada incorporada

En `static/assets/` se incluyen estos modelos `.glb`:

- `pitagoras.glb` (triángulo rectángulo y cuadrados)
- `identidad_algebraica.glb` (modelo de áreas del cuadrado de un binomio)
- `calculo_-2.glb`, `calculo_-1.glb`, `calculo_0.glb`, `calculo_1.glb`, `calculo_2.glb` (superficies paramétricas)

Los alumnos pueden rotarlos en el visor 3D y, en un teléfono compatible, pulsar **Ver en mi espacio (RA)**. La aplicación carga [model-viewer 4.4.0](https://modelviewer.dev/docs/index.html) desde un CDN; se requiere conexión a Internet para cargarlo salvo que se instale una copia local autorizada. La RA espacial depende además de las capacidades del navegador, del dispositivo y del protocolo HTTPS. **No se ha verificado la cámara en un teléfono físico dentro de este entorno de desarrollo.**

Los profesores pueden **elegir** qué modelo de la biblioteca acompaña a cada lección. Subir nuevos modelos 3D personalizados directamente desde la interfaz **no está implementado**: requiere un módulo adicional de carga, revisión y seguridad de archivos. También pueden enlazar material complementario usando URL HTTPS.

## 6. Progreso y evaluaciones

Alumnos inscritos pueden marcar lecciones como completadas, rendir prácticas/exámenes y consultar su progreso global y por asignatura. Los intentos se registran en SQLite. La calificación se comprueba en el backend; la clave de cada pregunta no sale en la respuesta inicial de un examen.

Si un examen tiene tiempo límite, el reloj mostrado es informativo: el backend aplica el límite. Volver a abrir un examen sin entregar recupera el intento en curso. Los intentos no enviados también cuentan para el límite, para que no pueda evadirse reiniciando la prueba.

Los profesores pueden ver inscripciones, avance por lecciones y resultados de sus propios estudiantes.

## 7. Configuración y seguridad

**Configuración → Mi perfil**: edita nombre y biografía. **Cambiar correo**, **Cambiar contraseña** y **Eliminar cuenta** piden contraseña actual. Un cambio de correo o contraseña revoca todas las sesiones existentes y obliga a iniciar sesión de nuevo. El borrado es irreversible y elimina los registros de la cuenta; al borrar una cuenta docente se eliminan sus cursos.

Seguridad incluida: contraseñas procesadas con `scrypt` y sal aleatoria; sesiones aleatorias persistidas como hashes; cookie `HttpOnly` y `SameSite=Lax`; protección CSRF; permisos verificados por servidor y por propiedad del curso; límite básico de intentos; restablecimiento de contraseña con token aleatorio, hash, validez de 30 minutos y un solo uso.

**Atención:** es una base razonable de seguridad para un **prototipo académico local**, no una plataforma auditada para Internet. Antes de operar públicamente con usuarios reales, añade verificación de correo, HTTPS obligatorio, endurecimiento de cabeceras/CSP, rate limiting compartido, copias de seguridad, monitoreo, pruebas de seguridad y aprobación institucional para el tratamiento de datos. Recomendaciones de referencia: [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet) y [OWASP Forgot Password](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).

## 8. Recuperación de contraseña

Haz clic en **¿Olvidaste tu contraseña?**. El sistema genera un enlace de recuperación de un solo uso.

- **En desarrollo local**, si no configuraste SMTP, el enlace se escribe en `data/dev_mail.log`. Es un buzón simulado local, no un correo enviado. Debes abrir ese archivo y copiar la URL.
- **Para correo real**, configura en `.env` `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS` y `PUBLIC_BASE_URL` con los datos autorizados por tu proveedor. Comprueba los envíos antes de permitir cuentas reales.

Nunca publiques el `.env`, `data/*.db` ni `data/*.log`.

## 9. Estudio del objetivo 4

Los estudiantes tienen un apartado de **Investigación** con pretest de 6 preguntas, postest y encuesta de percepción. Cada etapa se envía como máximo una vez por cuenta, y el postest exige haber completado antes el pretest. Estos formularios son **pilotos**, pendientes de revisión y aplicación del protocolo real antes de analizar su validez.

La exportación seudonimizada puede habilitarse asignando `ADMIN_EMAILS=investigador@universidad.edu.co` en `.env`; ese usuario autorizado accede a `http://127.0.0.1:8000/api/admin/export.csv`. No es un informe publicado de resultados. Puedes usar `analizar_resultados.py` con el CSV para obtener estadísticas descriptivas, sin generar valores inventados.

## 10. Comprobar el código

```powershell
python -m pytest -q tests
```

En el entorno de preparación pasaron las dos pruebas backend: autenticación, sesiones/CSRF, invitaciones docentes, separación de permisos, inscripción, lecciones, preguntas, calificación, informes, perfil, cambios de clave, recuperación y borrado. Además se verificó la sintaxis de JavaScript y se revisaron visualmente pantallas de ambos roles en un navegador de prueba **sin acceso directo al servidor**, mediante respuestas simuladas. La integración visual completa navegador-servidor y la RA espacial física siguen siendo pruebas pendientes para tu equipo.

## 11. Guía rápida de demostración ante el docente

1. Inicia el servidor local.
2. Crea una invitación y registra un profesor.
3. El profesor crea una materia de Geometría, añade una lección y asigna el modelo `pitagoras`.
4. Crea un examen con 2 o 3 preguntas, marca respuestas y publica curso y evaluación.
5. Cierra sesión y crea un alumno.
6. El alumno se inscribe en el curso (público o mediante código), abre la lección y observa el modelo 3D.
7. El alumno completa la lección y responde el examen.
8. En **Mi progreso**, ve las lecciones y los puntajes.
9. Inicia sesión como profesor y muestra su reporte del alumno.
10. Demuestra **Configuración** y explica el proceso de recuperación de contraseña local.

## 12. Archivos relevantes

```text
REAL_MATH_AR_ROLES_V3/
├── app.py                      API FastAPI, sesiones y SQLite
├── manage.py                   Invitaciones / alta de profesores
├── INICIAR_WINDOWS.bat          Ejecuta el proyecto en Windows
├── requirements.txt            Dependencias
├── .env.example                Configuración de muestra (sin contraseñas)
├── static/
│   ├── index.html              Estructura visual
│   └── assets/
│       ├── site.css            Diseño responsive
│       ├── app.js              Navegación, formularios y cliente API
│       └── *.glb               Biblioteca de modelos tridimensionales
├── tests/                      Pruebas automatizadas
├── docs/                       Pantallas de vista previa y lista de evidencias
├── generate_models.py          Regeneración matemática de modelos GLB
└── analizar_resultados.py      Estadísticos descriptivos de piloto
```

**Nota de migración:** si ya trabajaste con la base de datos de una versión anterior, haz primero una copia de seguridad. La aplicación intenta incorporar el campo `role` con valor `student` para cuentas antiguas y conservar el progreso previo, pero conviene validar la migración con una copia y no directamente sobre los datos únicos de trabajo.


## 8. Modo RA en la cámara del portátil (WebAR con AR.js)

El proyecto incorpora ahora dos modalidades complementarias:

1. **Visor 3D con `<model-viewer>`**: permite girar/acercar los modelos sin cámara.
2. **RA con webcam y marcador Hiro (AR.js 3.4.8 + A-Frame 1.6.0)**: al detectar un marcador físico, superpone el modelo `.glb` elegido sobre la imagen del entorno.

### Cómo probarlo

1. Arranca el servidor como siempre: `./.venv/Scripts/python.exe -m uvicorn app:app --reload` desde PowerShell (en Windows usa `\.\.venv\Scripts\python.exe` al inicio).
2. Entra a `http://127.0.0.1:8000` e inicia sesión.
3. Abre una lección que incluya un modelo (Geometría / Álgebra / Cálculo).
4. Pulsa **Activar RA con la cámara del portátil**. Se abrirá `/ar?model=...` en una pestaña independiente.
5. En otra pestaña abre **Ver e imprimir marcador Hiro** (también disponible en `/marcador`). Imprime la imagen oficial en blanco y negro.
6. Da permiso a Chrome para usar la cámara y presenta la hoja a unos 15–40 cm de la webcam, ajustando distancia e iluminación.
7. Si el marcador se detecta, el modelo del recurso aparecerá superpuesto a él. En la pantalla de RA puedes cambiar de recurso sin regresar al curso.

**Requisitos y límites:** conexión a Internet para cargar bibliotecas y el marcador oficial, webcam funcional, permiso de cámara en Chrome, navegador actualizado, abrir en `http://127.0.0.1:8000` o HTTPS. No funcionará si abres `ar.html` como `file:///...`. La aplicación no almacena el vídeo de la cámara. El funcionamiento con la cámara física todavía requiere validación en el portátil del estudiante; las pruebas automatizadas no lo sustituyen. Si no puede detectar la hoja, verifica que las bibliotecas carguen y que haya buena iluminación.

**Documentación metodológica:** se denomina *WebAR basada en marcadores*, implementada con AR.js y A-Frame. No debe llamarse RA espacial sin marcadores. Los archivos originales `.glb` sirven para ambos modos. La experiencia utiliza un solo marcador Hiro para todos los recursos, no uno diferente para cada modelo.

Más detalles: `docs/PRUEBA_RA_PORTATIL.md`.
