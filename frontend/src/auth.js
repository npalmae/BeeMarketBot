// Quién está conectado, y las llamadas que inician y cierran la sesión.
import { reactive } from 'vue'
import api from './api'
import router from './router'
import { sessionToken } from './session'

// El servidor responde 401 cuando la sesión caducó; el cliente HTTP lo anuncia y aquí se reacciona.
export const SESSION_EXPIRED = 'beemarketbot:session-expired'

export const auth = reactive({
  user: null,
  loaded: false,
  // Todavía no existe ninguna cuenta: la pantalla de entrada explica cómo crear la primera.
  setupNeeded: false
})

export const canEdit = () => auth.user && ['admin', 'editor'].includes(auth.user.role)
export const isAdmin = () => auth.user && auth.user.role === 'admin'

function adopt(user, token, setupNeeded = false) {
  auth.user = user
  sessionToken.value = token || null
  auth.setupNeeded = setupNeeded
}

export async function loadSession() {
  try {
    const res = await api.get('/api/auth/session')
    adopt(res.data?.user ?? null, res.data?.csrf, !!res.data?.setup_needed)
  } catch {
    adopt(null, null)
  }
  auth.loaded = true
  return auth.user
}

export async function login(username, password) {
  const res = await api.post('/api/auth/login', { username, password })
  adopt(res.data.user, res.data.csrf)
  return res.data.user
}

export async function logout() {
  try {
    await api.post('/api/auth/logout')
  } finally {
    adopt(null, null)
  }
}

window.addEventListener(SESSION_EXPIRED, () => {
  adopt(null, null)
  if (router.currentRoute.value.name !== 'Login') {
    router.push({ name: 'Login', query: { reason: 'expired', redirect: router.currentRoute.value.fullPath } })
  }
})
