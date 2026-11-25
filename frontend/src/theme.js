// Apariencia de la interfaz: oscura (la de siempre) o clara.
// La clase "light" en <html> cambia las variables del tema (ver styles/web2-theme.css).
// Un script en index.html la aplica antes del primer pintado, para que no parpadee.
import { reactive } from 'vue'

const STORAGE_KEY = 'theme'
const LIGHT_CLASS = 'light'

// La primera vez no hay preferencia guardada y se queda el modo oscuro.
export const theme = reactive({ light: document.documentElement.classList.contains(LIGHT_CLASS) })

function apply(light) {
  theme.light = !!light
  document.documentElement.classList.add('web2')
  document.documentElement.classList.toggle(LIGHT_CLASS, theme.light)
}

// El icono de la pestaña: en modo claro, la abeja clara no se vería sobre la pestaña.
const FAVICON_DARK = '/beemarketbot-icon.png'
const FAVICON_LIGHT = '/beemarketbot-icon-light.png'

function applyFavicon() {
  const link = document.querySelector('link[rel="icon"]')
  if (link) link.setAttribute('href', theme.light ? FAVICON_LIGHT : FAVICON_DARK)
}

// Se llama una sola vez al arrancar la aplicación, para que el icono acompañe al modo guardado.
export function initTheme() {
  applyFavicon()
}

export function setLight(light) {
  apply(light)
  applyFavicon()
  try {
    localStorage.setItem(STORAGE_KEY, theme.light ? 'light' : 'dark')
  } catch {
    // Sin almacenamiento disponible: el modo vale mientras la pestaña esté abierta.
  }
}

export const toggleTheme = () => setLight(!theme.light)
