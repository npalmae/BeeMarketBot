import { createRouter, createWebHistory } from 'vue-router'
import Home from '../views/Home.vue'
import Process from '../views/MainView.vue'
import SimulationView from '../views/SimulationView.vue'
import SimulationRunView from '../views/SimulationRunView.vue'
import ReportView from '../views/ReportView.vue'
import InteractionView from '../views/InteractionView.vue'
import LoginView from '../views/LoginView.vue'
import AccountView from '../views/AccountView.vue'
import UsersView from '../views/UsersView.vue'
import { auth, loadSession } from '../auth'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: Home
  },
  {
    path: '/process/:projectId',
    name: 'Process',
    component: Process,
    props: true
  },
  {
    path: '/simulation/:simulationId',
    name: 'Simulation',
    component: SimulationView,
    props: true
  },
  {
    path: '/simulation/:simulationId/start',
    name: 'SimulationRun',
    component: SimulationRunView,
    props: true
  },
  {
    path: '/report/:reportId',
    name: 'Report',
    component: ReportView,
    props: true
  },
  {
    path: '/interaction/:reportId',
    name: 'Interaction',
    component: InteractionView,
    props: true
  },
  {
    path: '/login',
    name: 'Login',
    component: LoginView,
    meta: { public: true }
  },
  {
    path: '/account',
    name: 'Account',
    component: AccountView
  },
  {
    path: '/users',
    name: 'Users',
    component: UsersView,
    meta: { roles: ['admin'] }
  },
  // Cualquier otra dirección vuelve a la portada (que a su vez pide entrar si hace falta).
  {
    path: '/:pathMatch(.*)*',
    redirect: '/'
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// Solo se vuelve a una dirección de la propia aplicación; nunca a un sitio ajeno.
function safeRedirect(value) {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') ? value : '/'
}

router.beforeEach(async (to) => {
  if (!auth.loaded) await loadSession()
  if (to.meta.public) {
    // Con la sesión abierta no hace falta volver a entrar.
    return auth.user ? { path: safeRedirect(to.query.redirect) } : true
  }
  if (!auth.user) {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
  if (to.meta.roles && !to.meta.roles.includes(auth.user.role)) {
    return { path: '/' }
  }
  // Una contraseña asignada por un administrador se cambia antes de usar la aplicación.
  if (auth.user.must_change_password && to.name !== 'Account') {
    return { name: 'Account', query: { required: '1', redirect: to.fullPath } }
  }
  return true
})

export default router
