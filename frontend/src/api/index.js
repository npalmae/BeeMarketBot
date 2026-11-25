import axios from 'axios'
import i18n from '../i18n'
import { sessionToken } from '../session'

// 创建axios实例
const service = axios.create({
  // Same origin by default: requests go through the dev-server proxy, so the browser
  // does not need to be on the same machine as the backend
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 300000, // 5分钟超时（本体生成可能需要较长时间）
  headers: {
    'Content-Type': 'application/json'
  }
})

// Sin token (sesión nueva o recién cerrada) se pide uno antes de cualquier cambio:
// el servidor solo acepta el token de la sesión que lo recibió.
async function ensureToken() {
  if (sessionToken.value) return
  const res = await service.get('/api/auth/session')
  sessionToken.value = res.data?.csrf || null
}

// 请求拦截器
service.interceptors.request.use(
  async config => {
    config.headers['Accept-Language'] = i18n.global.locale.value
    // Cada cambio va firmado con el token de la propia sesión (protección CSRF).
    const method = (config.method || 'get').toLowerCase()
    if (method !== 'get') {
      await ensureToken()
      if (sessionToken.value) {
        config.headers['X-CSRF-Token'] = sessionToken.value
      }
    }
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器（容错重试机制）
service.interceptors.response.use(
  response => {
    const res = response.data
    
    // 如果返回的状态码不是success，则抛出错误
    if (!res.success && res.success !== undefined) {
      console.error('API Error:', res.error || res.message || 'Unknown error')
      return Promise.reject(new Error(res.error || res.message || 'Error'))
    }
    
    return res
  },
  error => {
    console.error('Response error:', error)
    // Antes de expulsar al usuario, revalidamos la cookie. Esto evita que una
    // respuesta 401 aislada (proxy, pestaña antigua o token CSRF desfasado)
    // cierre la sesión visualmente cuando el servidor todavía la reconoce.
    const url = error.config?.url || ''
    if (
      error.response?.status === 401 &&
      !error.config?._authRetry &&
      !url.includes('/api/auth/login') &&
      !url.includes('/api/auth/session')
    ) {
      error.config._authRetry = true
      service.get('/api/auth/session')
        .then(sessionResponse => {
          const data = sessionResponse.data
          if (data?.user && data?.csrf) {
            sessionToken.value = data.csrf
            error.config.headers = error.config.headers || {}
            error.config.headers['X-CSRF-Token'] = data.csrf
            return service.request(error.config)
          }
          window.dispatchEvent(new CustomEvent('beemarketbot:session-expired'))
          return Promise.reject(error)
        })
        .catch(retryError => Promise.reject(retryError))
      return
    }
    const apiError = error.response?.data?.error || error.response?.data?.message
    
    // 处理超时
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      console.error('Request timeout')
    }
    
    // 处理网络错误
    if (error.message === 'Network Error') {
      console.error('Network error - please check your connection')
    }

    // Axios rejects non-2xx responses before the success interceptor can
    // surface the backend's safe, actionable error message.
    if (typeof apiError === 'string' && apiError) {
      error.message = apiError
    }
    
    return Promise.reject(error)
  }
)

export default service
