<template>
  <div class="login-page">
    <header class="login-header">
      <div class="brand">BEEMARKET<span class="brand-accent">BOT</span></div>
      <LanguageSwitcher />
    </header>

    <main class="authkit-page full">
      <section class="authkit-card">
        <p class="authkit-kicker">BeeMarketBot</p>
        <h1 class="authkit-title">{{ $t('auth.login.title') }}</h1>
        <p class="authkit-lead">{{ $t('auth.login.subtitle') }}</p>

        <p v-if="reason === 'expired'" class="authkit-message info" role="status">
          {{ $t('auth.error.loginRequired') }}
        </p>

        <div v-if="auth.setupNeeded" class="authkit-message info">
          <p>{{ $t('auth.login.noUsers') }}</p>
          <code>{{ $t('auth.login.noUsersCommand') }}</code>
        </div>

        <form v-else novalidate @submit.prevent="submit">
          <label class="authkit-field">
            <span>{{ $t('auth.login.username') }}</span>
            <input
              v-model.trim="username"
              name="username"
              autocomplete="username"
              :placeholder="$t('auth.login.usernamePlaceholder')"
              autofocus
            >
          </label>
          <label class="authkit-field">
            <span>{{ $t('auth.login.password') }}</span>
            <input v-model="password" name="password" type="password" autocomplete="current-password">
          </label>
          <p v-if="error" class="authkit-message error" role="alert">{{ error }}</p>
          <button class="authkit-button block" type="submit" :disabled="busy || !username || !password">
            {{ busy ? $t('auth.login.submitting') : $t('auth.login.submit') }}
          </button>
        </form>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'
import { auth, login } from '../auth'

const route = useRoute()
const router = useRouter()
// Los textos de la plantilla ($t) salen del idioma de la aplicación.
useI18n()

const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
const reason = computed(() => route.query.reason)

const submit = async () => {
  error.value = ''
  busy.value = true
  try {
    await login(username.value, password.value)
    password.value = ''
    // Solo se vuelve a una dirección de la propia aplicación.
    const target = route.query.redirect
    router.replace(typeof target === 'string' && target.startsWith('/') && !target.startsWith('//') ? target : '/')
  } catch (caught) {
    // El servidor ya traduce el motivo (credenciales, demasiados intentos).
    error.value = caught?.message || String(caught)
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  background: #050713;
  color: #F5F7FA;
  font-family: 'DM Mono', monospace;
}

.login-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  padding: 0 24px;
  border-bottom: var(--line);
  background: rgba(5, 7, 19, .9);
}

.brand {
  font-weight: 800;
  font-size: 18px;
  letter-spacing: -.02em;
}

.brand-accent { color: #76FA0A; }
</style>
