<template>
  <AppHeader />

  <main class="authkit-page">
    <section class="authkit-card wide">
      <p class="authkit-kicker">BeeMarketBot</p>
      <h1 class="authkit-title">{{ $t('auth.users.title') }}</h1>
      <p class="authkit-lead">{{ $t('auth.users.subtitle') }}</p>

      <div v-if="notice" :class="['authkit-message', notice.kind]" role="status">
        {{ notice.text }}
        <code v-if="notice.code">{{ notice.code }}</code>
      </div>
      <p v-if="error" class="authkit-message error" role="alert">{{ error }}</p>

      <form class="authkit-row" novalidate @submit.prevent="createUser">
        <label class="authkit-field">
          <span>{{ $t('auth.users.username') }}</span>
          <input v-model.trim="draft.username" name="new-username" autocomplete="off" spellcheck="false">
        </label>
        <label class="authkit-field">
          <span>{{ $t('auth.users.role') }}</span>
          <select v-model="draft.role">
            <option v-for="role in ROLES" :key="role" :value="role">{{ $t('auth.roles.' + role) }}</option>
          </select>
        </label>
        <label class="authkit-field">
          <span>{{ $t('auth.users.password') }}</span>
          <input v-model="draft.password" name="initial-password" autocomplete="off" spellcheck="false">
        </label>
        <button class="authkit-button ghost" type="button" @click="draft.password = generatePassword()">
          {{ $t('auth.users.generate') }}
        </button>
        <button class="authkit-button" type="submit" :disabled="busy">
          {{ busy ? $t('auth.users.creating') : $t('auth.users.create') }}
        </button>
      </form>

      <div class="authkit-table-wrap">
        <table class="authkit-table">
          <thead>
            <tr>
              <th>{{ $t('auth.users.username') }}</th>
              <th>{{ $t('auth.users.role') }}</th>
              <th>{{ $t('auth.users.state') }}</th>
              <th>{{ $t('auth.users.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="user in users" :key="user.id">
              <tr>
                <td>
                  <strong>{{ user.username }}</strong>
                  <span v-if="user.id === auth.user?.id" class="authkit-badge">{{ $t('auth.users.you') }}</span>
                </td>
                <td>
                  <select
                    class="authkit-input"
                    :value="user.role"
                    :disabled="busy"
                    :aria-label="$t('auth.users.role')"
                    @change="changeRole(user, $event.target)"
                  >
                    <option v-for="role in ROLES" :key="role" :value="role">{{ $t('auth.roles.' + role) }}</option>
                  </select>
                </td>
                <td>
                  <span class="authkit-badge" :class="{ off: user.disabled }">
                    {{ user.disabled ? $t('auth.users.disabledAccount') : $t('auth.users.active') }}
                  </span>
                  <span v-if="user.must_change_password" class="authkit-badge pending">
                    {{ $t('auth.users.mustChange') }}
                  </span>
                </td>
                <td>
                  <div class="authkit-actions">
                    <button class="authkit-button small ghost" type="button" :disabled="busy" @click="toggleDisabled(user)">
                      {{ user.disabled ? $t('auth.users.enable') : $t('auth.users.disable') }}
                    </button>
                    <button
                      v-if="user.id !== auth.user?.id"
                      class="authkit-button small"
                      type="button"
                      :disabled="busy"
                      @click="openReset(user)"
                    >
                      {{ $t('auth.users.reset') }}
                    </button>
                  </div>
                </td>
              </tr>
              <tr v-if="resetting === user.id">
                <td colspan="4">
                  <form class="authkit-row" novalidate @submit.prevent="savePassword(user)">
                    <label class="authkit-field">
                      <span>{{ $t('auth.users.newPassword') }}</span>
                      <input v-model="reset.password" autocomplete="off" spellcheck="false">
                    </label>
                    <button class="authkit-button ghost" type="button" @click="reset.password = generatePassword()">
                      {{ $t('auth.users.generate') }}
                    </button>
                    <button class="authkit-button" type="submit" :disabled="busy">{{ $t('auth.users.save') }}</button>
                    <button class="authkit-button ghost" type="button" :disabled="busy" @click="resetting = null">
                      {{ $t('auth.users.cancel') }}
                    </button>
                  </form>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import api from '../api'
import AppHeader from '../components/AppHeader.vue'
import { auth, loadSession } from '../auth'

const { t } = useI18n()
const router = useRouter()

const ROLES = ['admin', 'editor', 'viewer']
// Sin caracteres que se confundan al copiar a mano (0/O, 1/l/I). Son 56: entra
// exacto en los 64 valores de un trozo de número al azar, sin inclinar la elección.
const ALPHABET = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'

const users = ref([])
const busy = ref(false)
const error = ref('')
const notice = ref(null)
const resetting = ref(null)
const draft = reactive({ username: '', password: '', role: 'viewer' })
const reset = reactive({ password: '' })

function generatePassword(length = 16) {
  let out = ''
  while (out.length < length) {
    const values = new Uint32Array(length)
    crypto.getRandomValues(values)
    for (const value of values) {
      if (value % 64 < ALPHABET.length) out += ALPHABET[value % 64]
    }
  }
  return out.slice(0, length)
}

// Cada acción arranca sin avisos anteriores; los errores llegan ya traducidos del servidor.
async function run(job) {
  error.value = ''
  notice.value = null
  busy.value = true
  try {
    await job()
    return true
  } catch (caught) {
    error.value = caught?.message || String(caught)
    return false
  } finally {
    busy.value = false
  }
}

async function refresh() {
  const res = await api.get('/api/auth/users')
  users.value = res.data.users
}

// Si un administrador se cambia a sí mismo de nivel o se deshabilita, su propia
// sesión cambia: se vuelve a leer y se sale de esta pantalla si ya no le corresponde.
async function syncSelf(user) {
  if (user.id !== auth.user?.id) return
  const me = await loadSession()
  if (!me) router.replace({ name: 'Login' })
  else if (me.role !== 'admin') router.replace('/')
}

async function afterChange(user, key) {
  await refresh()
  notice.value = { kind: 'success', text: t(key, { username: user.username }) }
  await syncSelf(user)
}

const createUser = () =>
  run(async () => {
    await api.post('/api/auth/users', { ...draft })
    await refresh()
    // La contraseña inicial solo se muestra esta vez: el servidor no la devuelve.
    notice.value = {
      kind: 'success',
      text: `${t('auth.users.created', { username: draft.username })} ${t('auth.users.initialPassword')}`,
      code: draft.password
    }
    draft.username = ''
    draft.password = ''
  })

async function changeRole(user, select) {
  const role = select.value
  if (role === user.role) return
  const done = await run(() => api.patch(`/api/auth/users/${user.id}`, { role }).then(() => afterChange(user, 'auth.users.updated')))
  // El servidor no aceptó el cambio (por ejemplo, el único administrador): se vuelve al nivel real.
  if (!done) select.value = user.role
}

const toggleDisabled = user =>
  run(() =>
    api
      .patch(`/api/auth/users/${user.id}`, { disabled: !user.disabled })
      .then(() => afterChange(user, 'auth.users.updated'))
  )

const openReset = user => {
  error.value = ''
  notice.value = null
  reset.password = ''
  resetting.value = user.id
}

const savePassword = user =>
  run(async () => {
    await api.post(`/api/auth/users/${user.id}/password`, { new_password: reset.password })
    resetting.value = null
    reset.password = ''
    await afterChange(user, 'auth.users.passwordReset')
  })

onMounted(async () => {
  try {
    await refresh()
  } catch (caught) {
    error.value = caught?.message || String(caught)
  }
})
</script>
