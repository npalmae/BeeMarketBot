<template>
  <AppHeader />

  <main class="authkit-page">
    <section class="authkit-card wide">
      <p class="authkit-kicker">BeeMarketBot</p>
      <h1 class="authkit-title">{{ $t('models.title') }}</h1>
      <p class="authkit-lead">{{ $t('models.subtitle') }}</p>

      <div v-if="notice" :class="['authkit-message', notice.kind]" role="status">{{ notice.text }}</div>
      <p v-if="error" class="authkit-message error" role="alert">{{ error }}</p>

      <div v-if="!loaded" class="authkit-message info">{{ $t('common.loading') }}</div>

      <template v-else>
        <section v-for="kind in ['llm', 'embedding']" :key="kind" class="models-service">
          <h2 class="models-service-title">{{ $t('models.' + kind + '.title') }}</h2>
          <p class="models-service-lead">{{ $t('models.' + kind + '.lead') }}</p>

          <div class="authkit-row">
            <label class="authkit-field">
              <span>
                {{ $t('models.fields.baseUrl') }}
                <em class="authkit-badge">{{ sourceLabel(kind, 'base_url') }}</em>
              </span>
              <input
                v-model.trim="form[kind].base_url"
                class="authkit-input"
                type="text"
                :placeholder="placeholder(kind, 'base_url')"
                :disabled="busy"
              >
            </label>
            <label class="authkit-field">
              <span>
                {{ $t('models.fields.model') }}
                <em class="authkit-badge">{{ sourceLabel(kind, 'model') }}</em>
              </span>
              <input
                v-model.trim="form[kind].model"
                class="authkit-input"
                type="text"
                :placeholder="placeholder(kind, 'model')"
                :disabled="busy"
              >
            </label>
          </div>

          <div class="authkit-row">
            <label class="authkit-field">
              <span>
                {{ $t('models.fields.apiKey') }}
                <em class="authkit-badge">{{ sourceLabel(kind, 'api_key') }}</em>
              </span>
              <input
                v-model="form[kind].api_key"
                class="authkit-input"
                type="password"
                autocomplete="new-password"
                :placeholder="keyPlaceholder(kind)"
                :disabled="busy"
              >
            </label>
            <label v-if="kind === 'embedding'" class="authkit-field">
              <span>
                {{ $t('models.fields.dimensions') }}
                <em class="authkit-badge">{{ sourceLabel(kind, 'dimensions') }}</em>
              </span>
              <input
                v-model.trim="form.embedding.dimensions"
                class="authkit-input"
                type="number"
                min="1"
                :placeholder="placeholder('embedding', 'dimensions')"
                :disabled="busy"
              >
            </label>
          </div>

          <div class="authkit-row models-actions">
            <button class="authkit-button ghost" type="button" :disabled="busy" @click="probe(kind)">
              {{ testing === kind ? $t('models.testing') : $t('models.test') }}
            </button>
            <span v-if="results[kind]" :class="['models-test-result', results[kind].ok ? 'ok' : 'fail']">
              {{ results[kind].ok
                ? $t('models.testOk', { ms: results[kind].latency_ms, model: results[kind].model })
                : $t('models.testFailed', { error: results[kind].error }) }}
            </span>
          </div>
        </section>

        <div class="authkit-divider"></div>

        <div class="authkit-row">
          <button class="authkit-button" type="button" :disabled="busy" @click="save">
            {{ saving ? $t('models.saving') : $t('models.save') }}
          </button>
          <span class="models-note">{{ $t('models.note') }}</span>
        </div>
      </template>
    </section>
  </main>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppHeader from '../components/AppHeader.vue'
import { getModelsConfig, saveModelsConfig, testModelsConnection } from '../api/config'

const { t } = useI18n()

const loaded = ref(false)
const busy = ref(false)
const saving = ref(false)
const testing = ref(null)
const error = ref('')
const notice = ref(null)

// Lo que muestra el servidor por cada campo (origen, valor vigente, vista segura).
const state = ref({ llm: {}, embedding: {} })
// Lo que escribe el administrador: texto = valor nuevo; vacío = heredar del servidor.
const form = reactive({
  llm: { base_url: '', api_key: '', model: '' },
  embedding: { base_url: '', api_key: '', model: '', dimensions: '' }
})
const results = reactive({ llm: null, embedding: null })

const fieldMap = {
  llm: ['base_url', 'api_key', 'model'],
  embedding: ['base_url', 'api_key', 'model', 'dimensions']
}

const sourceLabel = (kind, field) => {
  const entry = state.value[kind]?.[field]
  if (!entry?.set) return ''
  return t(entry.source === 'interface' ? 'models.sourceInterface' : 'models.sourceServer')
}

const placeholder = (kind, field) => {
  const entry = state.value[kind]?.[field]
  return entry?.effective || t('models.inheritHint')
}

const keyPlaceholder = (kind) => {
  const entry = state.value[kind]?.api_key
  if (!entry?.set) return t('models.keyMissing')
  return entry.preview || ''
}

const load = async () => {
  const res = await getModelsConfig()
  state.value = res.data
  for (const kind of ['llm', 'embedding']) {
    for (const field of fieldMap[kind]) {
      // Los campos no secretos traen su valor de la interfaz; las claves nunca.
      form[kind][field] = field === 'api_key' ? '' : (state.value[kind]?.[field]?.value || '')
    }
  }
}

onMounted(async () => {
  try {
    await load()
    loaded.value = true
  } catch (err) {
    error.value = err.message
  }
})

const payloadFor = (kind) => {
  const body = {}
  for (const field of fieldMap[kind]) {
    if (field === 'api_key') {
      // Solo se envía una clave si el administrador escribió una nueva.
      if (form[kind].api_key) body.api_key = form[kind].api_key
    } else {
      body[field] = form[kind][field]
    }
  }
  return body
}

const probe = async (kind) => {
  testing.value = kind
  results[kind] = null
  error.value = ''
  notice.value = null
  try {
    const res = await testModelsConnection({ kind, config: payloadFor(kind) })
    results[kind] = res.data
  } catch (err) {
    results[kind] = { ok: false, error: err.response?.data?.data?.error || err.message }
  } finally {
    testing.value = null
  }
}

const save = async () => {
  saving.value = true
  busy.value = true
  error.value = ''
  notice.value = null
  try {
    const body = {}
    for (const kind of ['llm', 'embedding']) body[kind] = payloadFor(kind)
    const res = await saveModelsConfig(body)
    state.value = res.data
    for (const kind of ['llm', 'embedding']) form[kind].api_key = ''
    notice.value = { kind: 'success', text: t('models.saved') }
  } catch (err) {
    error.value = err.response?.data?.error || err.message
  } finally {
    saving.value = false
    busy.value = false
  }
}
</script>

<style scoped>
.models-service {
  margin-top: 28px;
}

.models-service-title {
  margin: 0 0 4px;
  font-size: 17px;
}

.models-service-lead {
  margin: 0 0 14px;
  font-size: 13px;
  color: var(--w2-muted);
}

.models-service .authkit-field em {
  font-style: normal;
  margin-left: 8px;
}

.models-actions {
  align-items: center;
}

.models-test-result {
  font-size: 12px;
}

.models-test-result.ok { color: var(--w2-brand); }
.models-test-result.fail { color: var(--w2-danger-soft); }

.models-note {
  align-self: center;
  font-size: 12px;
  color: var(--w2-muted);
  max-width: 460px;
}
</style>
