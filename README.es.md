# agentlog

**Tu historial de coding con IA, self-hosted.**

**🇪🇸 Español · 🇬🇧 [English](README.md)**

Respaldo local + visor de tus conversaciones con herramientas de IA-coding.

Este proyecto lee dónde cada herramienta guarda sus sesiones en tu disco, las
convierte a Markdown legible con metadata estándar, y te da un visor HTML
standalone para navegarlas, agruparlas, filtrarlas y ver analíticas de tu uso.

Todo corre **localmente en tu Mac**. Nada se sube a ningún lado. La interfaz del
visor es **bilingüe (inglés / español)** con un selector de idioma.

**▶ [Probá la demo en vivo](https://lucasgday.github.io/agentlog/)** — el visor
corriendo en tu navegador con datos de ejemplo, para ver cómo funciona. **No sube
nada.**

![agentlog — lista de conversaciones, analytics, historial, UI bilingüe](docs/hero.gif)

---

## Por qué

**Por defecto, tus herramientas de IA no conservan tu historial para siempre — y
rara vez te enterás cuando lo perdés.**

- **Claude Code** limpia transcripts viejos pasado un tiempo (por defecto, según
  la última actividad). Es **configurable**: subiendo `cleanupPeriodDays` en
  `~/.claude/settings.json` podés extender mucho la retención o, en la práctica,
  desactivarla. Pero si no lo tocaste, estás en el default y las sesiones viejas
  desaparecen.
- **Codex** solo lista las conversaciones recientes; las viejas dejan de aparecer
  aunque por un tiempo sigan en disco.
- Cada herramienta tiene su propia política, su propio formato y su propio
  alcance. Y si reinstalás, cambiás de máquina, corrés un `rm` o se corrompe una
  base de datos, ese historial se va **sin aviso** y sin papelera.

> **Nota honesta:** si usás *solo* Claude Code y subís `cleanupPeriodDays`, gran
> parte del borrado automático deja de ser un problema. Aun así, agentlog sigue
> aportando lo que un setting de retención no te da (ver abajo).

Lo que este proyecto te da, más allá de la retención de cada herramienta:

- **Una copia durable y aparte.** Acumulativa: una vez respaldada, una
  conversación **nunca se borra de tu copia**, aunque la herramienta de origen la
  elimine, reinstales o migres de máquina.
- **Un archivo único multi-herramienta.** Claude Code, Codex, Cowork, OpenCode y
  Cursor juntos en un mismo lugar y formato, no cinco silos con sus propias
  reglas.
- **Algo navegable de verdad.** Markdown legible + un visor con búsqueda,
  agrupación, filtros, analytics y marcado de revisadas — no `.jsonl`/SQLite
  crudos.

Esas conversaciones suelen guardar **decisiones de diseño, el porqué de cómo se
hizo algo, y contexto que tu código no registra**. La idea es tenerlo a salvo y a
mano.

Y como son tus datos privados, **todo corre local**: los scripts solo leen tus
archivos y escriben Markdown en tu disco, el visor es un HTML estático. No hay
servidor, ni nube, ni telemetría. (Ver [Privacidad](#privacidad).)

---

## Cómo se compara

agentlog nació de la misma idea que **Paxel** (de YC) — sacarle sentido a tus
sesiones de Claude Code / Codex / Cursor — pero con el default opuesto sobre tus
datos. Paxel corre el análisis local pero **sube datos derivados** a YC (extractos
de prompts, rutas de archivos, metadata de commits, narrativas) para armar un
perfil online; un audit de seguridad de la comunidad detectó que mandaba más de lo
anunciado, y bajaron la promo del lanzamiento por la polémica de privacidad
([audit](https://www.gate.com/news/detail/y-combinators-paxel-ai-tool-claims-local-analysis-but-security-audit-21668126),
[cobertura](https://digg.com/ai/urogjb9u)).

agentlog es **self-hosted y offline** — solo lee tus archivos locales y escribe
Markdown local. Nada, ni crudo ni derivado, sale de tu máquina.

| | agentlog | Paxel |
|---|---|---|
| Datos que salen de tu máquina | **Ninguno** | Datos derivados subidos a YC |
| Hosting | Self-hosted / offline | Nube (YC) |
| Frecuencia | **Continua** — corre a diario, incremental y acumulativa | Foto one-shot |
| Salida | Un archivo Markdown vivo + visor navegable | Un "builder profile" online, one-shot |
| Open source | Sí (MIT) | No |

---

## Qué hace

- **Respaldo incremental y acumulativo.** Procesa solo lo nuevo o lo que cambió
  desde la última corrida. Nunca borra markdowns ya generados, aunque la
  herramienta de origen borre la conversación original.
- **Conversión a Markdown.** Cada sesión queda como un `.md` con título, fecha,
  id, proyecto y fuente, y los turnos separados (`### You` / `### Claude`, etc.).
- **Visor HTML standalone** (`viewer.html`). Se abre con doble clic (`file://`),
  sin servidor. Agrupa por fuente o por proyecto, colorea por herramienta,
  filtra archivadas y revisadas, copia por turno o conversación entera, marca
  conversaciones como revisadas (progreso exportable/importable como JSON),
  muestra el historial de corridas y una vista de **analytics** (heatmap diario
  estilo GitHub, top proyectos, actividad en el tiempo por día/semana/mes con
  toggle de conversaciones/turnos). Interfaz bilingüe (EN/ES).
- **Respaldo automático diario** vía `launchd` (opcional).

---

## Fuentes soportadas

| Herramienta  | Origen en disco                                                        |
|--------------|------------------------------------------------------------------------|
| Claude Code  | `~/.claude/projects/*/*.jsonl`                                          |
| Codex        | `~/.codex/sessions` y `~/.codex/archived_sessions`                      |
| Cowork       | `~/Library/Application Support/Claude/local-agent-mode-sessions`        |
| OpenCode     | `~/.local/share/opencode/opencode.db`                                   |
| Cursor       | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`   |

### No soportadas (y por qué)

- **Antigravity** — guarda las sesiones en un formato protobuf propietario sin
  esquema público, así que no hay forma estable de parsearlas.
- **claude.ai** (la app web) — las conversaciones viven en la nube de Anthropic,
  no quedan en tu disco, por lo que no hay nada local que respaldar.

---

## Instalación

Requiere **macOS** y **Python 3** (viene con macOS).

**1. Conseguí el código** — cloná el repo (o descargá el ZIP desde el botón verde
**Code** en GitHub y descomprimilo):

```bash
git clone https://github.com/lucasgday/agentlog.git
cd agentlog
```

**2. Dales permiso de ejecución a los scripts:**

```bash
chmod +x update-backup.sh *.command
```

### Correr el respaldo a mano

```bash
./update-backup.sh
```

Por defecto la base es la carpeta donde vive el script. Podés pasar otra ruta
como primer argumento si querés guardar los markdowns en otro lado:

```bash
./update-backup.sh ~/mis-respaldos
```

**Opciones** (corré `./update-backup.sh --help` para la lista completa):

```bash
./update-backup.sh --only claude,codex   # solo algunas fuentes
./update-backup.sh --dry-run             # previsualiza qué cambiaría, no escribe nada
```

También podés hacer **doble clic** en `update-backup.command`.

### Activar el respaldo automático (diario, 12:00)

Doble clic en `install-auto.command`. Instala una tarea de `launchd` que corre el
respaldo todos los días al mediodía (o al despertar la Mac si estaba dormida).

Para quitarla: doble clic en `uninstall-auto.command`.

---

## Usar el visor

Abrí **`viewer.html`** con doble clic (se abre en el navegador como `file://`)
y apuntalo a la carpeta donde están tus `markdown-*` (la misma carpeta base del
respaldo). Desde ahí podés navegar, filtrar, copiar y ver las analíticas.

Por defecto agrupa por **proyecto**, oculta las archivadas y las ya revisadas, y
abre la conversación activa más reciente. Podés cambiar el agrupamiento (por
herramienta), los filtros y el **idioma (EN/ES)** desde la barra superior / lateral.

---

## Capturas

> Usan **datos de ejemplo generados**, no conversaciones reales.

**Lista de conversaciones** — por defecto agrupadas por proyecto, con los turnos
y los bloques de herramientas renderizados.

![Vista de lista](docs/screenshots/es/main-list.png)

**Analytics** — resumen, heatmap diario del último año, top proyectos y
actividad en el tiempo (día/semana/mes, conversaciones o turnos).

![Analytics](docs/screenshots/es/analytics.png)

**Historial de corridas** — cada respaldo queda registrado, con cuántas
conversaciones nuevas aportó cada fuente.

![Historial de corridas](docs/screenshots/es/run-history.png)

---

## Privacidad

- **Nada sale de tu máquina.** Los scripts solo leen archivos locales y escriben
  Markdown local; el visor es un HTML estático que abrís con `file://`. Sin
  llamadas de red, sin servidor, sin telemetría.
- **El repo NO incluye ninguna conversación.** El `.gitignore` excluye todas las
  carpetas de markdown, los datos crudos (`*.jsonl`, `*.db`, `*.vscdb`, `*.pb`) y
  el estado de sincronización. Cada quien respalda **sus propias** conversaciones
  localmente; nunca se commitea contenido real.
- **Ni siquiera la demo hosteada sube nada.** GitHub Pages solo sirve HTML
  estático; cualquier carpeta que abras se lee en tu navegador vía File API y no
  se manda a ningún lado — el visor no hace ninguna llamada de red. Aun así, una
  página hosteada se baja en cada visita, así que para el uso real de todos los
  días conviene el `viewer.html` local (`file://`): es fijo y 100% inspeccionable.

---

## Contribuir

**Las sugerencias y aportes son bienvenidos** — sobre todo para **soportar más
herramientas de IA-coding**. Si la que usás guarda sus sesiones en disco y no
está en la lista, abrí un *issue* o mandá un *pull request*.

Sumar una fuente nueva es acotado: alcanza con un conversor que lea ese origen y
escriba el mismo Markdown estándar que usan los demás —

```
# <título>

<!-- date: <ISO> | id: <id> | project: <proyecto> | source: <fuente> | archived: <true|false> -->

### You

…

### <Asistente>

…
```

Una vez que el conversor produce ese formato, el visor y el resto del flujo lo
toman sin cambios. Fijate en cualquiera de los `converters/convert_*.py` como
referencia. También son bienvenidos reportes de bugs, mejoras al visor e ideas en
general.

---

## Notas

- **macOS-only por ahora.** Usa `launchd` y rutas propias de macOS para ubicar
  los orígenes. Portarlo a Linux/Windows implicaría ajustar esas rutas y el
  mecanismo de tarea automática.

---

## Licencia

MIT — ver [LICENSE](LICENSE).
