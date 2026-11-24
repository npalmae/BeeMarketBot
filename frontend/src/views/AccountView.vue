<template>
  <AppHeader />

  <main class="authkit-page">
    <section class="authkit-card">
      <p class="authkit-kicker">BeeMarketBot</p>
      <h1 class="authkit-title">{{ $t('auth.account.title') }}</h1>

      <template v-if="auth.user">
        <div class="authkit-facts">
          <div>
            <span>{{ $t('auth.login.username') }}</span>
            <strong>{{ auth.user.username }}</strong>
          </div>
          <div>
            <span>{{ $t('auth.roles.label') }}</span>
            <strong>{{ $t('auth.roles.' + auth.user.role) }}</strong>
          </div>
        </div>
        <p class="authkit-lead">
          {{ $t('auth.roles.' + auth.user.role + 'Desc') }}
          <template v-if="created"> {{ $t('auth.account.memberSince', { date: created }) }}</template>
        </p>

        <p v-if="auth.user.must_change_password" class="authkit-message info" role="status">
          {{ $t('auth.account.mustChange') }}
        </p>
        <p v-if="changed" class="authkit-message success" role="status">{{ $t('auth.account.changed') }}</p>

        <form novalidate @submit.prevent="submit">
          <label class="authkit-field">
            <span>{{ $t('auth.account.currentPassword') }}</span>
            <input v-model="currentPassword" type="password" autocomplete="current-password">
          </label>
          <label class="authkit-field">
            <span>{{ $t('auth.account.newPassword') }}</span>
            <input v-model="newPassword" type="password" autocomplete="new-password">
          </label>
          <label class="authkit-field">
            <span>{{ $t('auth.account.confirmPassword') }}</span>
            <input v-model="confirmation" type="password" autocomplete="new-password">
          </label>
          <p v-if="error" class="authkit-message error" role="alert">{{ error }}</p>
          <button class="authkit-button" type="submit" :disabled="busy">
            {{ busy ? $t('auth.account.changing') : $t('auth.account.change') }}
          </button>
        </form>
      </template>
    </section>
  </main>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '../api'
import AppHeader from '../components/AppHeader.vue'
import { auth } from '../auth'

const { t } = useI18n()

const currentPassword = ref('')
const newPassword = ref('')
const confirmation = ref('')
const busy = ref(false)
const error = ref('')
const changed = ref(false)

const created = computed(() => {
  const raw = auth.user?.created_at
  if (!raw) return ''
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
})

const submit = async () => {
  error.value = ''
  changed.value = false
  if (newPassword.value !== confirmation.value) {
    error.value = t('auth.account.mismatch')
    return
  }
  busy.value = true
  try {
    const res = await api.post('/api/auth/password', {
      current_password: currentPassword.value,
      new_password: newPassword.value
    })
    // El servidor devuelve la cuenta actualizada (por ejemplo, sin cambio pendiente).
    auth.user = res.data.user
    currentPassword.value = ''
    newPassword.value = ''
    confirmation.value = ''
    changed.value = true
  } catch (caught) {
    error.value = caught?.message || String(caught)
  } finally {
    busy.value = false
  }
}
</script>
