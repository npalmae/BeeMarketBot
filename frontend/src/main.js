import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
import { initTheme } from './theme'
// Estilo visual Web2 neobrutalista (desde SIXMANAGER)
import './styles/web2.css'
import './styles/web2-pages.css'
// Interfaz de la app adaptada a ese estilo (solo aplica con la clase "web2")
import './styles/web2-theme.generated.css'
import './styles/web2-theme.css'
// Pantallas de acceso (entrar, mi cuenta, usuarios), escritas directamente en el estilo Web2
import './styles/auth.css'

document.documentElement.classList.add('web2')

const app = createApp(App)

app.use(router)
app.use(i18n)
// El modo de color ya lo aplicó index.html; aquí se acomoda el icono de la pestaña.
initTheme()

app.mount('#app')
