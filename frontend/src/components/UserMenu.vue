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
      <li v-if="auth.user?.role === 'admin'" class="menu-option" @click="go('Models')">
        {{ $t('auth.menu.models') }}
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
  border: 1px solid rgb(var(--w2-strong-rgb) / .5);
  color: var(--w2-text);
  font-family: 'DM Mono', monospace;
  font-size: 11px;
  letter-spacing: .08em;
  text-transform: uppercase;
  cursor: pointer;
  transition: border-color .2s, background .2s, color .2s;
}

.menu-trigger:hover,
.menu-trigger:focus-visible {
  border-color: var(--w2-invert-bg);
  background: var(--w2-invert-bg);
  color: var(--w2-invert-text);
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
  border: 1px solid var(--w2-invert-text);
  background: var(--w2-invert-bg);
  box-shadow: 6px 6px 0 var(--w2-accent);
}

.menu-option {
  padding: 8px 12px;
  color: var(--w2-invert-text);
  font-size: 11px;
  letter-spacing: .08em;
  text-transform: uppercase;
  white-space: nowrap;
  cursor: pointer;
}

.menu-option:hover {
  background: rgb(var(--w2-accent-rgb) / .18);
}

.menu-option.leave {
  margin-top: 4px;
  padding-top: 10px;
  border-top: 1px solid rgb(var(--w2-accent-rgb) / .45);
  color: var(--w2-invert-accent);
}

/* En pantallas angostas la cabecera va justa: el menú muestra solo el nombre. */
@media (max-width: 640px) {
  .user-role { display: none; }

  .menu-trigger { padding: 6px 8px; gap: 6px; }
}
</style>
