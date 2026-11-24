<div align="center">

<img src="./frontend/src/assets/logo/beemarketbot-logo.jpg" alt="BeeMarketBot" width="360"/>

# BeeMarketBot

**Predice todo.** Sube un documento, formula una pregunta y obtén un informe a partir de una simulación social con agentes de IA.

Español | [English](./README-EN.md) | [中文](./README-ZH.md)

</div>

## Qué hace

BeeMarketBot construye un mundo simulado a partir de un documento y lo deja
evolucionar para responder preguntas del tipo "¿qué pasaría si…?".

Tú subes los materiales de partida (informes, datos, cualquier texto en PDF, MD
o TXT) y describes en lenguaje natural lo que quieres saber. El sistema hace el
resto en cinco pasos:

1. **Construcción del grafo.** Extrae las entidades y las relaciones del documento
   y arma un grafo de conocimiento con memoria, en el que se busca por significado.
2. **Configuración del entorno.** Genera los perfiles de los agentes a partir de
   esas entidades y les entrega su memoria y su configuración.
3. **Simulación.** Los agentes conviven en dos plataformas sociales, una tipo
   Twitter y otra tipo Reddit, y reaccionan entre sí durante las rondas que
   definas, mientras su memoria se actualiza con lo que ocurre.
4. **Informe.** Un agente de informes recorre la simulación con herramientas de
   consulta y escribe un informe con la respuesta.
5. **Interacción.** Puedes conversar con cualquier agente del mundo simulado o
   con el agente de informes para profundizar.

Tú solo tienes que subir los materiales y describir la pregunta. BeeMarketBot
devuelve un informe y el mundo simulado, abierto para seguir preguntando.

La interfaz está en español (predeterminado), inglés y chino, y el modelo
responde en el idioma elegido.

Las simulaciones hacen muchas llamadas al modelo. Conviene empezar con pocas
rondas (menos de 40) y aumentar después.

## Acceso

Cada página y cada ruta de la API pide iniciar sesión. No hay registro público
ni contraseña por defecto. Hay tres niveles de acceso:

| Nivel | Qué puede hacer |
|---|---|
| `admin` | Todo, y además administrar las cuentas (`/users`) |
| `editor` | Crear y ejecutar simulaciones, generar informes |
| `viewer` | Solo consultar |

El primer administrador se crea una sola vez, desde la carpeta del proyecto:

```bash
# Con Docker: muestra la contraseña inicial una sola vez
docker compose exec -T beemarketbot sh -c "cd backend && uv run --frozen python -m app.auth create-user --username <nombre> --role admin --generate-password"

# Sin Docker: pide la contraseña
cd backend && uv run python -m app.auth create-user --username <nombre> --role admin
```

Esa contraseña se cambia en el primer ingreso. A partir de ahí, las cuentas se
crean en **Usuarios**, dentro del menú del administrador, o con la
[línea de comandos](#administración-de-cuentas).

A las cuentas de solo consulta se les muestran apagados los controles que cambian algo
(crear un proyecto, iniciar una simulación, generar un informe, conversar con los
agentes). Abrir una pantalla tampoco dispara ninguna acción.

## Despliegue con Docker

Necesitas Docker y Docker Compose v2. Se levantan dos contenedores: la
aplicación y la base de grafos.

```bash
git clone https://github.com/npalmae/BeeMarketBot.git
cd BeeMarketBot

# 1. Configuración
cp .env.example .env
#    Edita .env: modelo, embeddings y SECRET_KEY (openssl rand -hex 32)

# 2. Construir y arrancar (aplicación + FalkorDB)
docker compose up -d --build

# 3. Crear el primer administrador (ver «Acceso»)

# 4. Comprobar la memoria en grafos con tu configuración real
docker compose exec beemarketbot sh -c "cd backend && uv run --frozen python scripts/validate_graph_memory.py"
```

La aplicación lee `.env` de la carpeta raíz y expone el puerto `3000` (interfaz) y
el `5001` (API). Si están tomados, define `WEB_PORT` y `API_PORT` en `.env`.
FalkorDB queda accesible solo desde el equipo, en `127.0.0.1:6379` (cambia el
puerto con `FALKORDB_PORT`).

Los datos se conservan fuera del contenedor: `./backend/uploads` (proyectos,
simulaciones, informes, estado de la memoria y cuentas) y el volumen
`falkordb-data` (el grafo). Para actualizar, vuelve a ejecutar
`docker compose up -d --build`.

## Despliegue desde el código

| Herramienta | Versión | Para |
|---|---|---|
| Node.js | 20.19 o posterior (o 22.12 y posterior) | Interfaz |
| Python | 3.11 o 3.12 | Backend |
| uv | reciente | Entorno de Python |
| FalkorDB | 6.0.x (o Neo4j 5.26 y posterior) | Base de grafos de la memoria |

```bash
# Dependencias (raíz + frontend + backend)
npm run setup:all

# Base de grafos (solo la primera vez)
docker run -d --name falkordb -p 127.0.0.1:6379:6379 -v falkordb-data:/data falkordb/falkordb:6.0.4

# Interfaz y backend
npm run dev
```

La interfaz queda en `http://localhost:3000` y la API en `http://localhost:5001`.
La configuración es la misma `.env` de la carpeta raíz.

## Configuración

Todas las variables se definen en `.env`; `.env.example` las explica una por una.

| Variable | Obligatoria | Para |
|---|---|---|
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL_NAME` | sí | Modelo de lenguaje. Sirve cualquier proveedor con formato de API de OpenAI. |
| `GRAPH_DB_URL` | sí | Base de grafos: FalkorDB (`falkordb://host:6379`) o Neo4j (`neo4j://usuario:clave@host:7687`). Con Docker ya viene definida. |
| `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` | sí | Modelo que convierte el texto en vectores y tamaño de esos vectores. |
| `SECRET_KEY` | en un servidor | Firma las sesiones. Sin ella se genera una al arrancar y todos deben volver a entrar tras cada reinicio. |
| `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL` | no | Solo si los embeddings los da un proveedor distinto del modelo. |
| `GRAPH_LLM_*` | no | Otro modelo para la extracción del grafo, y otros ajustes de la memoria. |
| `LLM_TEMPERATURE`, `LLM_EXTRA_BODY` | no | Proveedores que solo aceptan una temperatura fija o piden parámetros propios. |
| `LLM_BOOST_*` | no | Un segundo proveedor para la simulación en paralelo, y así disponer de más llamadas simultáneas. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | no | Crea el primer administrador al arrancar, solo si todavía no hay cuentas. |
| `SESSION_HOURS`, `COOKIE_SECURE`, `CORS_ORIGINS` | no | Duración de la sesión (24 horas), cookies solo por HTTPS, orígenes externos que pueden llamar a la API. |

## Administración de cuentas

Los comandos se ejecutan desde `backend/` como `uv run python -m app.auth <comando>`.

| Comando | Qué hace |
|---|---|
| `create-user --username <nombre> --role <nivel>` | Crea una cuenta; deberá cambiar la contraseña al entrar. |
| `list-users` | Lista las cuentas. |
| `set-password --username <nombre>` | Asigna una contraseña nueva; deberá cambiarla al entrar. |
| `set-role --username <nombre> --role <nivel>` | Cambia el nivel de acceso. |
| `disable --username <nombre>` | Impide que la cuenta entre. |
| `enable --username <nombre>` | Vuelve a permitir que la cuenta entre. |

Con Docker se ejecutan dentro del contenedor:

```bash
docker compose exec -T beemarketbot sh -c "cd backend && uv run --frozen python -m app.auth list-users"
```

La contraseña nunca se pasa como argumento: se pide en el terminal, se lee de la
entrada estándar (`--password-stdin`) o se genera (`--generate-password`, que la
muestra una sola vez).

Las cuentas se guardan en `backend/uploads/auth/users.db`, junto al resto de los
datos, con las contraseñas cifradas (scrypt). Los intentos fallidos de ingreso
tienen límite. Si la aplicación se sirve por HTTP, la contraseña viaja sin
cifrar: ponle HTTPS si se usa desde fuera de una red de confianza.

## Desarrollo

```bash
# Pruebas del backend
cd backend && uv run python -m pytest -q tests

# Compilar la interfaz
cd frontend && npm run build

# Regenerar el estilo de la interfaz después de cambiar un componente
cd frontend && node scripts/build-web2-theme.mjs
```

Las pruebas nunca escriben en la carpeta de datos real. Cinco de ellas comprueban
la memoria contra una base de grafos real y solo se ejecutan si se define
`GRAPH_MEMORY_TEST_URL`.

```
backend/app/api           rutas de la API
backend/app/auth          cuentas, sesión y niveles de acceso
backend/app/graph_memory  memoria en grafos (Graphiti)
backend/app/services      ontología, grafo, simulación e informe
backend/scripts           ejecutores de la simulación y validación de la memoria
backend/tests            pruebas del backend
frontend/src              interfaz (Vue 3)
locales                   textos de la interfaz y de los mensajes
```

## Licencia y origen

BeeMarketBot es software libre bajo la
[GNU Affero General Public License v3.0](./LICENSE). Es un derivado de
[MiroFish](https://github.com/666ghj/MiroFish), de 666ghj. La simulación social
se apoya en [OASIS](https://github.com/camel-ai/oasis) (camel-ai) y la memoria en
grafos en [Graphiti](https://github.com/getzep/graphiti).
