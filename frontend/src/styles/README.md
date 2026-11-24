# Estilo visual del frontend

La interfaz usa el estilo Web2 de sixmanager.com. Está armado en tres capas,
en el orden en que las carga `src/main.js`:

| Archivo | Origen | Qué hace |
|---|---|---|
| `web2.css`, `web2-pages.css` | Kit de sixmanager.com | Paleta, tipografías y componentes del sitio (`.site-header`, `.hero`, `.primary-button`…). Son los archivos originales, sin modificar. |
| `web2-theme.generated.css` | Generado | Traduce los colores y las tipografías de cada componente `.vue` a la paleta Web2. No se edita a mano. |
| `web2-theme.css` | Manual | Lo que la traducción automática no puede decidir: titulares, botones y tarjetas con sombra dura, variables de la portada y excepciones. |
| `auth.css` | Manual | Pantallas de acceso (entrar, mi cuenta, usuarios): tarjeta, campos, botones, avisos y tabla con clases `authkit-*`, escritas directamente en la paleta Web2. No dependen de `html.web2`. |

Las pantallas de acceso (`LoginView`, `AccountView`, `UsersView`, `AppHeader`,
`UserMenu`) y la marca de las cabeceras (`BrandMark`: el hexágono junto al nombre)
están en la lista `SKIP` del generador: no se traducen dos veces.

## Cómo se activa

`main.js` agrega la clase `web2` a `<html>` y todo el tema está escrito bajo
`html.web2`. Si se quita esa clase, la interfaz vuelve a su aspecto claro original.

## Regenerar la capa generada

Cada vez que cambien los estilos de un componente (por una edición propia o por
traer cambios desde MiroFish):

```bash
cd frontend && node scripts/build-web2-theme.mjs
```

El script repite cada regla con su atributo `data-v-…` de Vue, para que siga
aplicando solo a su componente, y calcula ese atributo para los modos de
desarrollo y de producción. Al terminar imprime un informe con las superficies
oscuras que vuelve azules por defecto: conviene revisar si alguna debe seguir
oscura (consolas, bloques de código) y reservarla en `web2-theme.css`.

## Cómo escribir en `web2-theme.css`

- Usar el prefijo `html.web2 #app`, que gana a los estilos scoped de los
  componentes y a la capa generada. Para lo que se monta fuera de `#app` (el
  modal de historial), usar `html.web2 body …`.
- Incluir siempre la clase raíz de la pantalla. Nombres como `.step-num`,
  `.section-title` o `.brand` se repiten entre vistas y se mezclarían:
  `.home-container`, `.main-view`, `.graph-panel`, `.history-database`,
  `.language-switcher`, `.workbench-panel`, `.env-setup-panel`,
  `.simulation-panel`, `.report-panel`, `.interaction-panel`.
- Usar la paleta del kit: `var(--ink)`, `var(--paper)`, `var(--lime)`,
  `var(--purple)`, `var(--coral)`, `var(--green)` y `var(--line)`.
- Sombras duras, sin desenfoque: `6px 6px 0 var(--purple)`. Al pasar el cursor,
  el elemento se hunde (`translate(4px, 4px)`) y la sombra desaparece.

## Límites

- Los colores que JavaScript asigna de forma directa (atributos SVG del grafo)
  solo se cambian con reglas CSS específicas. Ya están cubiertos los enlaces y
  los textos del grafo; los nodos conservan su color por tipo de entidad.
