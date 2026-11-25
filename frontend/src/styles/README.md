# Estilo visual del frontend

La interfaz usa el estilo Web2 de sixmanager.com y tiene dos modos de color:
oscuro (el predeterminado) y claro. Está armado en capas, en el orden en que las
carga `src/main.js`:

| Archivo | Origen | Qué hace |
|---|---|---|
| `web2.css`, `web2-pages.css` | Kit de sixmanager.com | Tipografías y componentes del sitio (`.site-header`, `.hero`, `.primary-button`…). Son los archivos originales, sin modificar. |
| `web2-theme.generated.css` | Generado | Repite cada regla con color o tipografía de los componentes `.vue`, escrita con las variables del tema. No se edita a mano. |
| `web2-theme.css` | Manual | Las variables del tema (sección 1) y lo que la traducción automática no puede decidir: titulares, botones y tarjetas con sombra dura, y las cabeceras. |
| `auth.css` | Manual | Pantallas de acceso (entrar, mi cuenta, usuarios): tarjeta, campos, botones, avisos y tabla con clases `authkit-*`. |

## Los dos modos

Todas las hojas del tema usan **variables**, nunca colores escritos. Están
definidas al principio de `web2-theme.css`:

- `html.web2 { --w2-… }`: modo oscuro.
- `html.web2.light { --w2-… }`: modo claro, con otros valores para las mismas
  variables (fondos blancos, texto oscuro).

`main.js` agrega la clase `web2` a `<html>`. El botón `ThemeSwitch.vue` (en todas
las cabeceras) agrega o quita `light` y guarda la elección en `localStorage`
(`theme`). Un script en `index.html` la aplica antes del primer pintado, para que
no parpadee.

Las variables, por papel:

| Variable | Para |
|---|---|
| `--w2-bg`, `--w2-bg-rgb` | Fondo de la página. |
| `--w2-surface`, `--w2-surface-rgb`, `--w2-surface-2`, `--w2-hover` | Tarjetas y paneles; la variante algo más alta (filas al pasar el puntero); el fondo de los controles al pasar. |
| `--w2-field` | Campos de texto. |
| `--w2-text`, `--w2-text-rgb` | Texto. La versión `-rgb` (una tripleta `r g b`) sirve para transparencias: `rgb(var(--w2-text-rgb) / .72)`. |
| `--w2-strong`, `--w2-strong-rgb` | Línea o texto con el máximo contraste contra la página (bordes al pasar el puntero). |
| `--w2-accent`, `--w2-accent-rgb`, `--w2-accent-soft`, `--w2-accent-soft-rgb` | El azul de acento y su tono suave (rótulos, bordes). |
| `--w2-danger`, `--w2-danger-rgb`, `--w2-ok`, `--w2-ok-rgb`, `--w2-warn-rgb`, `--w2-warn-text`, `--w2-purple`, `--w2-purple-rgb` | Colores de estado y sus tintes de fondo. |
| `--w2-danger-soft`, `--w2-ok-soft`, `--w2-purple-soft` | Tonos con que se escribe sobre los tintes de estado (avisos, insignias). |
| `--w2-brand`, `--w2-brand-rgb` | Verde de la marca. |
| `--w2-on-accent` | Texto sobre un relleno de acento. |
| `--w2-invert-bg`, `--w2-invert-hover`, `--w2-invert-text`, `--w2-invert-text-rgb`, `--w2-invert-accent` | Superficies invertidas (desplegables, bloque destacado): claras en el modo oscuro y oscuras en el claro. |
| `--w2-console-bg` | Consolas y bloques de código: oscuros en los dos modos. |
| `--w2-ink` | Negro de la tinta; no cambia con el modo. |
| `--w2-min-text-a` | Transparencia mínima del texto atenuado. En claro vale `.62`: sobre blanco, un gris de `.55` no llega al contraste mínimo. |

Para escribir un color nuevo, elegir la variable por su papel y no por su tono:
`var(--w2-invert-text)` es oscuro en un modo y claro en el otro, y eso es lo que
se busca. Las reglas de una sola pieza que deba verse igual en los dos modos (la
placa negra del logo, el texto blanco sobre el morado del hover) sí llevan el
color escrito, con un comentario que lo diga.

## Regenerar la capa generada

Cada vez que cambien los estilos de un componente (por una edición propia o por
traer cambios desde MiroFish):

```bash
cd frontend && node scripts/build-web2-theme.mjs
```

El script repite cada regla con su atributo `data-v-…` de Vue, para que siga
aplicando solo a su componente, y calcula ese atributo para los modos de
desarrollo y de producción. Escribe dos cosas:

- **Modo oscuro:** cada color se cambia por su variable (`#333` pasa a ser texto,
  un fondo claro pasa a ser superficie).
- **Solo modo claro** (al final de cada archivo, bajo `html.web2.light`): lo que
  el modo oscuro deja tal cual porque ya se lee bien sobre fondo oscuro (un gris
  con tinte, una etiqueta de color) y sobre blanco no. Además, los fondos de
  color saturado se marcan como "islas": en modo claro, su texto vuelve a tomar
  los colores del modo oscuro.

Al terminar imprime un informe. Conviene revisar las superficies oscuras que
vuelve azules por defecto: si alguna debe seguir oscura (consolas, bloques de
código), reservarla en `web2-theme.css`.

## Archivos fuera del generador

La lista `SKIP` del generador deja fuera lo que ya está escrito con las
variables del tema y no necesita traducción: las pantallas de acceso
(`LoginView`, `AccountView`, `UsersView`), sus piezas (`AppHeader`, `UserMenu`),
la marca de las cabeceras (`BrandMark`) y el botón de modo (`ThemeSwitch`).

## Cómo escribir en `web2-theme.css`

- Usar el prefijo `html.web2 #app`, que gana a los estilos scoped de los
  componentes y a la capa generada. Para lo que se monta fuera de `#app` (el
  modal de historial), usar `html.web2 body …`.
- Incluir siempre la clase raíz de la pantalla. Nombres como `.step-num`,
  `.section-title` o `.brand` se repiten entre vistas y se mezclarían:
  `.home-container`, `.main-view`, `.graph-panel`, `.history-database`,
  `.language-switcher`, `.workbench-panel`, `.env-setup-panel`,
  `.simulation-panel`, `.report-panel`, `.interaction-panel`.
- No escribir colores: usar las variables de la sección 1.
- Sombras duras, sin desenfoque: `6px 6px 0 var(--w2-purple)`. Al pasar el cursor,
  el elemento se hunde (`translate(4px, 4px)`) y la sombra desaparece. Sobre el
  morado del hover el texto va siempre en blanco.

## Límites

- Los colores que JavaScript asigna de forma directa (atributos SVG del grafo)
  solo se cambian con reglas CSS específicas. Ya están cubiertos los enlaces y
  los textos del grafo; los nodos conservan su color por tipo de entidad.
- Las reglas anidadas dentro de los estilos de un componente no pasan por el
  generador. Si una se añade, conviene escribirla ya con las variables del tema.
