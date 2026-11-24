<template>
  <div class="user-menu" ref="menuRef">
    <button class="menu-trigger" type="button" @click.stop="open = !open">
      <span class="user-name">{{ auth.user?.username }}</span>
      <span class="user-role">{{ auth.user ? $t('auth.roles.' + auth.user.role) : '' }}</span>
      <span class="caret">{{ open ? '▲' : '▼' }}</span>
    </button>
    <ul v-if="open" class="menu-dropdown">
      <li class="menu-option" @click="go('Account')">{{ $t('auth.menu.account') }}</li>
      <li v-if="auth.user?.role === 'admin'" class="menu-option" @click="go('Users')">
        {{ $t('auth.menu.users') }}
      </li>
      <li class="menu-option leave" @click="signOut">{{ $t('auth.menu.logout') }}</li>
    </ul>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { auth, logout } from '../auth'

// Los textos de la plantilla ($t) salen del idioma de la aplicación.
useI18n()

const router = useRouter()
const open = ref(false)
const menuRef = ref(null)

const go = (name) => {
  open.value = false
  router.push({ name })
}

const signOut = async () => {
  open.value = false
  // Aunque el servidor no responda, la sesión se abandona también aquí.
  try {
    await logout()
  } catch (error) {
    console.warn('No se pudo cerrar la sesión en el servidor:', error)
  }
  router.push({ name: 'Login' })
}

const onClickOutside = (event) => {
  if (menuRef.value && !menuRef.value.contains(event.target)) open.value = false
}

onMounted(() => document.addEventListener('click', onClickOutside))
onUnmounted(() => document.removeEventListener('click', onClickOutside))
</script>

<style scoped>
/* Mismo aspecto que el selector de idioma bajo el tema Web2. */
.user-menu {
  position: relative;
  display: inline-block;
  font-family: 'DM Mono', monospace;
}

.menu-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: transparent;
  border: 1px solid rgba(255, 255, 255, .5);
  color: #F5F7FA;
  font-family: 'DM Mono', monospace;
  font-size: 11px;
  letter-spacing: .08em;
  text-transform: uppercase;
  cursor: pointer;
  transition: border-color .2s, background .2s, color .2s;
}

.menu-trigger:hover,
.menu-trigger:focus-visible {
  border-color: #fff;
  background: #fff;
  color: #050713;
}

.user-name {
  font-weight: 700;
}

.user-role {
  opacity: .65;
}

.caret {
  font-size: .6rem;
}

.menu-dropdown {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 60;
  min-width: 220px;
  margin: 0;
  padding: 4px 0;
  list-style: none;
  border: 1px solid #050713;
  background: #F5F7FA;
  box-shadow: 6px 6px 0 #4689C8;
}

.menu-option {
  padding: 8px 12px;
  color: #050713;
  font-size: 11px;
  letter-spacing: .08em;
  text-transform: uppercase;
  white-space: nowrap;
  cursor: pointer;
}

.menu-option:hover {
  background: rgba(70, 137, 200, .18);
}

.menu-option.leave {
  margin-top: 4px;
  padding-top: 10px;
  border-top: 1px solid rgba(70, 137, 200, .45);
  color: #761472;
}

/* En pantallas angostas la cabecera va justa: el menú muestra solo el nombre. */
@media (max-width: 640px) {
  .user-role { display: none; }

  .menu-trigger { padding: 6px 8px; gap: 6px; }
}
</style>
