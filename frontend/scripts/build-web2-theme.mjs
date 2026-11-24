#!/usr/bin/env node
/**
 * Genera src/styles/web2-theme.generated.css.
 *
 * Toma cada regla con color o tipografía de los bloques <style> de los
 * componentes .vue y la repite con la paleta del estilo Web2 de SIXMANAGER,
 * bajo el alcance `html.web2`. Los componentes originales no se modifican.
 *
 * Los selectores conservan el atributo `data-v-…` de Vue, para que cada regla
 * siga aplicando solo a su componente. Ese atributo se calcula igual que en
 * @vue/compiler-sfc: sha256 de la ruta del archivo (desarrollo) y de la ruta
 * seguida del código fuente (producción). Se generan ambas variantes.
 *
 * Uso (desde frontend/):  node scripts/build-web2-theme.mjs
 * Los ajustes a mano van en src/styles/web2-theme.css, que se carga después
 * y gana por especificidad (usa `html.web2 #app …`).
 */
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const SRC = join(ROOT, 'src')
const OUT = join(SRC, 'styles', 'web2-theme.generated.css')

/** Vistas que el router no usa: no se generan reglas para ellas. */
const SKIP = new Set(['views/Process.vue'])

// ---------------------------------------------------------------- paleta web2
const P = {
  ink: '#050713', paper: '#0D1228', paper2: '#111936', hover: '#252E5A',
  lime: '#4689C8', soft: '#7FC4FF', text: '#F5F7FA'
}
const line = a => `rgba(70, 137, 200, ${a})`
const dim = a => `rgba(245, 247, 250, ${a})`
/** Tintes de fondo por familia de color, en formato "r, g, b". */
const TINT = {
  red: '215, 19, 67', orange: '215, 19, 67', pink: '215, 19, 67',
  yellow: '245, 158, 11', green: '85, 158, 68',
  blue: '70, 137, 200', purple: '118, 20, 114'
}
const MONO = "'DM Mono', monospace"
const SERIF = "Georgia, 'Times New Roman', serif"

// ------------------------------------------------------------- colores
const NAMED = {
  white: '#ffffff', black: '#000000', red: '#ff0000', green: '#008000', blue: '#0000ff',
  yellow: '#ffff00', orange: '#ffa500', purple: '#800080', gray: '#808080', grey: '#808080',
  silver: '#c0c0c0', maroon: '#800000', navy: '#000080', teal: '#008080', cyan: '#00ffff',
  magenta: '#ff00ff'
}
const COLOR_SRC =
  '#[0-9a-fA-F]{8}\\b|#[0-9a-fA-F]{6}\\b|#[0-9a-fA-F]{3,4}\\b|\\brgba?\\([^)]*\\)|\\bhsla?\\([^)]*\\)|' +
  `\\b(${Object.keys(NAMED).join('|')})\\b`
const COLOR_ALL = new RegExp(COLOR_SRC, 'g')
const COLOR_ONE = new RegExp(COLOR_SRC)

function hslToRgb(h, s, l) {
  h = ((h % 360) + 360) % 360
  const c = (1 - Math.abs(2 * l - 1)) * s
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = l - c / 2
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
    : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x]
  return { r: Math.round((r + m) * 255), g: Math.round((g + m) * 255), b: Math.round((b + m) * 255) }
}

/** Analiza un color: canal alfa, luminosidad HSL y familia de tono. */
function describe(raw) {
  const t = raw.trim().toLowerCase()
  if (t === 'transparent') return { transparent: true, raw }
  let rgb
  if (t[0] === '#') {
    let h = t.slice(1)
    if (h.length === 3 || h.length === 4) h = [...h].map(c => c + c).join('')
    const n = i => parseInt(h.slice(i, i + 2), 16)
    rgb = { r: n(0), g: n(2), b: n(4), a: h.length === 8 ? n(6) / 255 : 1 }
  } else {
    const fn = t.match(/^(rgba?|hsla?)\(([^)]*)\)$/)
    if (fn) {
      const v = fn[2].split(/[\s,/]+/).filter(Boolean).map(Number)
      rgb = { r: v[0], g: v[1], b: v[2], a: v.length > 3 ? v[3] : 1 }
      if (fn[1][0] === 'h') rgb = { ...hslToRgb(v[0], v[1] / 100, v[2] / 100), a: rgb.a }
    } else if (t in NAMED) {
      return describe(NAMED[t])
    } else {
      return null
    }
  }
  const { r, g, b } = rgb
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const chroma = max - min
  let h = 0
  if (chroma) {
    if (max === r) h = ((g - b) / chroma) % 6
    else if (max === g) h = (b - r) / chroma + 2
    else h = (r - g) / chroma + 4
    h = Math.round(h * 60)
    if (h < 0) h += 360
  }
  const family = chroma <= 30 ? 'neutral'
    : h < 15 || h >= 345 ? 'red' : h < 45 ? 'orange' : h < 70 ? 'yellow'
      : h < 165 ? 'green' : h < 255 ? 'blue' : h < 320 ? 'purple' : 'pink'
  return { ...rgb, l: (max + min) / 510, family, raw }
}

// -------------------------------------------------- traducción por papel
// Cada función devuelve el color nuevo, o null para conservar el original.
const MAP = {
  /** Superficies claras pasan a azul marino; las oscuras, al acento. */
  bg(c) {
    if (c.transparent) return 'transparent'
    if (c.family === 'neutral') {
      if (c.a < 1) {
        if (c.l < 0.5) {
          return c.a < 0.2 ? dim(Math.min(0.12, +c.a.toFixed(3)))
            : `rgba(5, 7, 19, ${Math.min(0.9, +(c.a + 0.2).toFixed(3))})`
        }
        return `rgba(13, 18, 40, ${c.a})`
      }
      if (c.l >= 0.965) return P.paper
      if (c.l >= 0.88) return P.paper2
      if (c.l >= 0.4) return P.hover
      return P.lime
    }
    if (c.a < 1 || c.l < 0.55) return null
    return `rgba(${TINT[c.family]}, ${c.l >= 0.85 ? '.16' : '.30'})`
  },
  /** Texto oscuro pasa a claro; los grises, a texto atenuado. */
  fg(c) {
    if (c.transparent) return 'transparent'
    if (c.family === 'neutral') {
      if (c.a < 1) return `rgba(245, 247, 250, ${c.a})`
      if (c.l < 0.35) return P.text
      if (c.l < 0.62) return dim('.72')
      if (c.l < 0.93) return dim('.55')
      return P.text
    }
    if (c.a < 1) return null
    if (c.family === 'orange') return P.soft
    if (c.family === 'red' && c.l < 0.42) return '#FF7A90'
    if (c.family === 'green' && c.l < 0.42) return '#8CD97C'
    if (c.family === 'blue' && c.l < 0.3) return P.soft
    return null
  },
  /** Bordes claros pasan a la línea azul; los oscuros, al acento suave. */
  bd(c) {
    if (c.transparent) return 'transparent'
    if (c.family === 'neutral') {
      if (c.a < 1) {
        return c.l < 0.5 ? dim(Math.min(0.3, +(c.a * 1.4).toFixed(3))) : `rgba(245, 247, 250, ${c.a})`
      }
      if (c.l >= 0.985) return dim('.35')
      if (c.l >= 0.7) return line('.38')
      if (c.l >= 0.3) return dim('.28')
      return P.soft
    }
    return c.family === 'orange' ? P.soft : null
  }
}

/** Sustituye var(...), url(...) etc. por marcadores para no tocar sus colores internos. */
function maskFunctions(value, names) {
  const hidden = []
  const re = new RegExp(`\\b(?:${names.join('|')})\\(`, 'g')
  let out = ''
  let i = 0
  let m
  while ((m = re.exec(value))) {
    out += value.slice(i, m.index)
    let depth = 0
    let k = m.index + m[0].length - 1
    for (; k < value.length; k++) {
      if (value[k] === '(') depth++
      else if (value[k] === ')') {
        depth--
        if (depth === 0) break
      }
    }
    hidden.push(value.slice(m.index, k + 1))
    out += `\u0000${hidden.length - 1}\u0000`
    i = k + 1
    re.lastIndex = i
  }
  out += value.slice(i)
  return { masked: out, restore: s => s.replace(/\u0000(\d+)\u0000/g, (_, n) => hidden[n]) }
}

function splitTop(value, sep) {
  const parts = []
  let depth = 0
  let buf = ''
  for (const ch of value) {
    if (ch === '(' || ch === '[') depth++
    else if (ch === ')' || ch === ']') depth--
    if (ch === sep && depth === 0) {
      parts.push(buf)
      buf = ''
    } else {
      buf += ch
    }
  }
  parts.push(buf)
  return parts
}

/** Reemplaza cada color de un valor según su papel. Devuelve null si no cambia. */
function mapColors(value, role) {
  const { masked, restore } = maskFunctions(value, ['var', 'url', 'env', 'constant', 'attr'])
  let changed = false
  const out = masked.replace(COLOR_ALL, token => {
    const c = describe(token)
    if (!c) return token
    const mapped = MAP[role](c)
    if (!mapped || mapped === token) return token
    changed = true
    return mapped
  })
  return changed ? restore(out) : null
}

/**
 * Sombras: las difusas desaparecen (el estilo usa sombras duras, que se
 * agregan a mano); las duras, como los anillos de foco, conservan su color traducido.
 */
function mapShadow(value) {
  const { masked, restore } = maskFunctions(value, ['var'])
  let changed = false
  const out = splitTop(masked, ',').map(layer => {
    if (!layer.match(COLOR_ONE)) return layer
    // Desplazamientos y radios son los números fuera de las funciones de color
    // (el tercero es el desenfoque; un 0 inicial puede no llevar unidad).
    const numbers = layer.replace(COLOR_ALL, '').match(/-?\d*\.?\d+/g) || []
    const blurred = numbers.length > 2 && parseFloat(numbers[2]) !== 0
    // Las difusas se vuelven invisibles; las duras conservan su color traducido.
    const mapped = blurred
      ? layer.replace(COLOR_ALL, t => (describe(t) ? 'transparent' : t))
      : mapColors(layer, 'fg')
    if (mapped === layer) return layer
    changed = true
    return mapped
  })
  return changed ? restore(out.join(', ')) : null
}

function extractColor(value) {
  const { masked } = maskFunctions(value, ['var'])
  const m = masked.match(COLOR_ONE)
  return m ? m[0] : ''
}

// ------------------------------------------------------------- tipografía
function mapFamily(value) {
  const s = value.toLowerCase()
  if (/inherit|initial|unset|revert/.test(s)) return null
  if (s.includes('times') || (s.includes('serif') && !s.includes('sans-serif'))) return SERIF
  if (s.includes('sans-serif') || s.includes('system-ui') || s.includes('monospace')) return MONO
  return null
}

// ------------------------------------------------------------- declaraciones
const FG_PROPS = new Set([
  'color', '-webkit-text-fill-color', 'caret-color', '-webkit-text-stroke-color',
  'text-emphasis-color', 'text-decoration-color', 'fill', 'stroke'
])
const COLOR_LONGHANDS = new Set([
  'border-color', 'outline-color', 'column-rule-color', 'border-top-color', 'border-right-color',
  'border-bottom-color', 'border-left-color', 'border-block-color', 'border-block-start-color',
  'border-block-end-color', 'border-inline-color', 'border-inline-start-color', 'border-inline-end-color'
])
const SHORTHAND = /^(border|outline|column-rule|text-decoration)(?:-(top|right|bottom|left|block-start|block-end|block|inline-start|inline-end|inline))?$/

/** Enmascara toda función salvo las de color: url(), gradientes, var()… pasan a ser un solo token. */
function maskImages(value) {
  const hidden = []
  const re = /\b([a-zA-Z-]+)\(/g
  let out = ''
  let i = 0
  let m
  while ((m = re.exec(value))) {
    if (/^(rgba?|hsla?)$/.test(m[1].toLowerCase())) continue
    out += value.slice(i, m.index)
    let depth = 0
    let k = m.index + m[0].length - 1
    for (; k < value.length; k++) {
      if (value[k] === '(') depth++
      else if (value[k] === ')') {
        depth--
        if (depth === 0) break
      }
    }
    hidden.push(value.slice(m.index, k + 1))
    out += `\u0000${hidden.length - 1}\u0000`
    i = k + 1
    re.lastIndex = i
  }
  out += value.slice(i)
  return { masked: out, restore: s => s.replace(/\u0000(\d+)\u0000/g, (_, n) => hidden[n]) }
}

const COLOR_FULL = new RegExp(`^(?:${COLOR_SRC})$`)

/** Saca el color suelto de una capa de `background` (fuera de url() y de gradientes). */
function splitBackgroundLayer(layer) {
  const { masked, restore } = maskImages(layer)
  const tokens = masked.split(/\s+/).filter(Boolean)
  let color = null
  for (let i = tokens.length - 1; i >= 0; i--) {
    if (tokens[i].match(COLOR_FULL)) {
      color = tokens[i]
      tokens.splice(i, 1)
      break
    }
  }
  return { color, image: restore(tokens.join(' ')) }
}

/**
 * Traduce una declaración `prop: value`. Devuelve las declaraciones
 * equivalentes (solo color y tipografía). Los shorthands se emiten como su
 * propiedad de color, para no pisar longhands declarados después
 * (p. ej. `border: 1px solid #ccc` seguido de `border-top: none`).
 */
function themeDeclaration(text) {
  const colon = text.indexOf(':')
  if (colon < 0) return []
  const prop = text.slice(0, colon).trim()
  let value = text.slice(colon + 1).trim()
  let important = false
  if (/!\s*important$/.test(value)) {
    important = true
    value = value.replace(/!\s*important$/, '').trim()
  }
  const p = prop.toLowerCase()
  const emit = (name, mapped) => (mapped ? [[name, mapped, important]] : [])

  if (p.startsWith('--')) return []
  if (p === 'font-family') return emit(p, mapFamily(value))
  if (FG_PROPS.has(p)) return emit(p, mapColors(value, 'fg'))
  if (p === 'background-color' || p === 'background-image') return emit(p, mapColors(value, 'bg'))
  if (p === 'background') {
    const out = []
    const layers = splitTop(value, ',').map(splitBackgroundLayer)
    if (layers.every(l => l.color)) {
      const colors = layers.map(l => l.color)
      const each = colors.map(c => mapColors(c, 'bg'))
      if (each.some(Boolean)) {
        out.push(['background-color', each.map((m, i) => m || colors[i]).join(', '), important])
      }
    }
    if (layers.some(l => l.image)) {
      const mapped = mapColors(layers.map(l => l.image || 'none').join(', '), 'bg')
      if (mapped) out.push(['background-image', mapped, important])
    }
    return out
  }
  if (COLOR_LONGHANDS.has(p)) return emit(p, mapColors(value, 'bd'))
  const sh = p.match(SHORTHAND)
  if (sh) {
    const color = extractColor(value)
    if (!color) return []
    return emit(`${p}-color`, mapColors(color, sh[1] === 'text-decoration' ? 'fg' : 'bd'))
  }
  if (p === 'box-shadow' || p === 'text-shadow') return emit(p, mapShadow(value))
  return []
}

// ------------------------------------------------------------- análisis CSS
function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, m => m.replace(/[^\n]/g, ' '))
}

/** Convierte una hoja en nodos: { kind: 'decl', text } y { kind: 'block', prelude, children, raw }. */
function parseBlock(src, i) {
  const nodes = []
  let buf = ''
  let start = i
  let paren = 0
  const flush = () => {
    const t = buf.trim()
    buf = ''
    if (t) nodes.push({ kind: 'decl', text: t })
  }
  while (i < src.length) {
    const ch = src[i]
    if (ch === '"' || ch === "'") {
      let j = i + 1
      while (j < src.length && src[j] !== ch) {
        if (src[j] === '\\') j++
        j++
      }
      buf += src.slice(i, j + 1)
      i = j + 1
      continue
    }
    if (ch === '(') paren++
    else if (ch === ')') paren--
    if (!paren && ch === '{') {
      const prelude = buf.trim()
      buf = ''
      const r = parseBlock(src, i + 1)
      nodes.push({ kind: 'block', prelude, children: r.nodes, raw: src.slice(start, r.index) })
      start = r.index
      i = r.index
      continue
    }
    if (!paren && ch === '}') {
      flush()
      return { nodes, index: i + 1 }
    }
    if (!paren && ch === ';') {
      flush()
      start = i + 1
      i++
      continue
    }
    buf += ch
    i++
  }
  flush()
  return { nodes, index: i }
}

/** Separa un selector en compuestos (strings) y combinadores ({ comb }). */
function splitSelector(sel) {
  const tokens = []
  let buf = ''
  let depth = 0
  const flush = () => {
    if (buf.trim()) tokens.push(buf.trim())
    buf = ''
  }
  for (const ch of sel) {
    if (ch === '(' || ch === '[') depth++
    else if (ch === ')' || ch === ']') depth--
    if (!depth && /\s/.test(ch)) {
      flush()
      tokens.push({ comb: ' ' })
      continue
    }
    if (!depth && /[>+~]/.test(ch)) {
      flush()
      tokens.push({ comb: ch })
      continue
    }
    buf += ch
  }
  flush()
  const parts = []
  for (const t of tokens) {
    const prev = parts[parts.length - 1]
    if (typeof t === 'string') {
      if (prev !== undefined && typeof prev === 'string') parts.push({ comb: ' ' })
      parts.push(t)
    } else if (prev !== undefined) {
      if (typeof prev === 'object') {
        if (t.comb !== ' ') prev.comb = t.comb
      } else {
        parts.push({ comb: t.comb })
      }
    }
  }
  if (typeof parts[parts.length - 1] === 'object') parts.pop()
  return parts
}

const attr = id => `[data-v-${id}]`
const PSEUDO_ELEMENT = /(?:::[\w-]+(?:\([^)]*\))?|:(?:before|after|first-line|first-letter))$/
const DEEP = /^(.*?)(?::deep|::v-deep)\((.*)\)(.*)$/

function withAttr(comp, id) {
  const pe = comp.match(PSEUDO_ELEMENT)
  return pe ? comp.slice(0, pe.index) + attr(id) + pe[0] : comp + attr(id)
}

/** Reproduce el selector que genera Vue para un bloque scoped. */
function compileScoped(sel, id) {
  const parts = splitSelector(sel)
  const out = []
  let last = -1
  let deep = false
  for (let i = 0; i < parts.length; i += 2) {
    const comp = parts[i]
    const m = comp.match(DEEP)
    if (m) {
      const before = m[1].trim()
      const inner = m[2].trim()
      const after = m[3].trim()
      if (before) {
        out.push(before)
        last = out.length - 1
      }
      if (inner || after) {
        if (out[out.length - 1] !== ' ') out.push(' ')
        out.push(inner + after)
      }
      deep = true
      break
    }
    out.push(comp)
    last = out.length - 1
    if (i + 1 < parts.length) out.push(parts[i + 1].comb === ' ' ? ' ' : ` ${parts[i + 1].comb} `)
  }
  if (deep && last < 0) out.unshift(attr(id))
  else if (last >= 0) out[last] = withAttr(out[last], id)
  return out.join('')
}

/** Selector de un bloque sin scoped: se limita a la app y al tema activo. */
function globalSelector(sel) {
  const s = sel.replace(/\s+/g, ' ').trim()
  if (/^#app\b/.test(s)) return `html.web2 ${s}`
  if (/^html\b/.test(s)) return s.replace(/^html/, 'html.web2')
  if (/^:root\b/.test(s)) return s.replace(/^:root/, 'html.web2')
  if (/^body\b/.test(s)) return `html.web2 ${s}`
  const first = splitSelector(s)[0]
  return /[.#]/.test(first) ? `html.web2 #app ${s}` : `html.web2 body ${s}`
}

/** Reglas de un bloque de estilo. Solo conserva las que tienen algo traducible. */
function collect(nodes, ctx, block) {
  for (const n of nodes) {
    if (n.kind !== 'block') continue
    if (n.prelude.startsWith('@')) {
      const name = n.prelude.match(/^@([\w-]+)/)[1]
      if (['media', 'supports', 'container', 'layer', 'scope'].includes(name)) {
        collect(n.children, [...ctx, n.prelude], block)
      } else if (COLOR_ONE.test(n.raw)) {
        const colors = (n.raw.match(COLOR_ALL) || []).length
        block.file.skipped.push(`${n.prelude.split('{')[0].trim()} (${colors} colores)`)
      }
      continue
    }
    const decls = n.children.filter(c => c.kind === 'decl').map(c => c.text)
    const themed = []
    for (const d of decls) {
      themed.push(...themeDeclaration(d))
      // informe: superficies oscuras, que el tema vuelve azules por defecto
      const p = d.slice(0, d.indexOf(':')).trim().toLowerCase()
      if (p === 'background' || p === 'background-color') {
        const c = describe(extractColor(d))
        if (c && !c.transparent && c.family === 'neutral' && c.l < 0.4 && c.a === 1) {
          block.file.review.push(`${n.prelude.replace(/\s+/g, ' ').slice(0, 64)} | ${d.replace(/\s+/g, ' ').slice(0, 56)}`)
        }
      }
    }
    if (themed.length) block.rules.push({ ctx, selector: n.prelude, decls: themed })
    if (n.children.some(c => c.kind === 'block')) block.file.nested.push(n.prelude)
  }
}

// ------------------------------------------------------------- recorrido
function walk(dir, base = '') {
  return readdirSync(dir, { withFileTypes: true }).flatMap(e => {
    const rel = base ? `${base}/${e.name}` : e.name
    return e.isDirectory() ? walk(join(dir, e.name), rel) : rel.endsWith('.vue') ? [rel] : []
  })
}

function scopeIds(rel, source) {
  const hash = s => createHash('sha256').update(s).digest('hex').slice(0, 8)
  return [...new Set([hash(rel), hash(rel + source)])]
}

const files = walk(SRC)
  .filter(f => !SKIP.has(f))
  .sort()
  .map(rel => {
    const source = readFileSync(join(SRC, rel), 'utf8')
    const file = {
      rel, ids: scopeIds(`src/${rel}`, source), blocks: [], skipped: [], nested: [], review: [],
      custom: [...new Set([...source.matchAll(/(--[\w-]+)\s*:\s*[^;}]+/g)].map(x => x[0].replace(/\s+/g, ' ')))]
    }
    for (const m of source.matchAll(/<style([^>]*)>([\s\S]*?)<\/style>/g)) {
      const block = { file, scoped: /\bscoped\b/.test(m[1]), rules: [] }
      file.blocks.push(block)
      collect(parseBlock(stripComments(m[2]), 0).nodes, [], block)
    }
    return file
  })

// ------------------------------------------------------------- salida
function selectorFor(rule, block) {
  const singles = splitTop(rule.selector, ',').map(s => s.replace(/\s+/g, ' ').trim()).filter(Boolean)
  const list = block.scoped
    ? singles.flatMap(s => block.file.ids.map(id => `html.web2 ${compileScoped(s, id)}`))
    : singles.map(globalSelector)
  return [...new Set(list)]
}

/** Escribe las reglas agrupando las que comparten el mismo @media / @supports. */
function emitRules(out, rules, block) {
  const open = []
  const indent = () => '  '.repeat(open.length)
  for (const r of rules) {
    let same = 0
    while (same < open.length && same < r.ctx.length && open[same] === r.ctx[same]) same++
    while (open.length > same) {
      open.pop()
      out.push(`${indent()}}`)
    }
    while (open.length < r.ctx.length) {
      out.push(`${indent()}${r.ctx[open.length]} {`)
      open.push(r.ctx[open.length])
    }
    out.push(selectorFor(r, block).map(l => indent() + l).join(',\n') + ' {')
    for (const [p, v, imp] of r.decls) out.push(`${indent()}  ${p}: ${v}${imp ? ' !important' : ''};`)
    out.push(`${indent()}}`)
  }
  while (open.length) {
    open.pop()
    out.push(`${indent()}}`)
  }
}

const out = [
  '/* ============================================================================',
  '   web2-theme.generated.css — NO EDITAR A MANO',
  '',
  '   Generado por scripts/build-web2-theme.mjs a partir de los bloques <style>',
  '   de los componentes .vue. Repite cada regla con color o tipografía usando',
  '   la paleta del estilo Web2 de SIXMANAGER, y solo cuando <html> tiene la',
  '   clase "web2". Los componentes originales no se modifican.',
  '',
  '   Regenerar (desde frontend/):  node scripts/build-web2-theme.mjs',
  '   Ajustes a mano:               src/styles/web2-theme.css (se carga después)',
  `   Vistas ignoradas (sin uso):   ${[...SKIP].join(', ')}`,
  '   ============================================================================ */'
]

const summary = []
for (const file of files) {
  let count = 0
  out.push('')
  out.push(`/* -------------------------------------------------- ${file.rel} */`)
  for (const block of file.blocks) {
    if (!block.rules.length) continue
    count += block.rules.length
    out.push('')
    emitRules(out, block.rules, block)
  }
  summary.push({ file: file.rel, rules: count, ...file })
}

writeFileSync(OUT, out.join('\n') + '\n')

// ------------------------------------------------------------- informe
const total = summary.reduce((n, s) => n + s.rules, 0)
console.log(`Generado ${relative(ROOT, OUT)}: ${total} reglas, ${out.length} líneas.`)
for (const s of summary) {
  const notes = [
    s.skipped.length && `${s.skipped.length} at-rules con color omitidas`,
    s.nested.length && `${s.nested.length} reglas anidadas omitidas`,
    s.custom.length && `variables: ${s.custom.join(' · ')}`
  ].filter(Boolean)
  console.log(`  ${String(s.rules).padStart(4)}  ${s.file}${notes.length ? `   [${notes.join(']; [')}]` : ''}`)
  s.skipped.forEach(x => console.log(`          omitida: ${x}`))
}
const review = summary.flatMap(s => s.review.map(r => `  ${s.file} :: ${r}`))
console.log(`\nSuperficies oscuras que el tema vuelve azules por defecto (${review.length}) — revisar:`)
review.forEach(r => console.log(r))
