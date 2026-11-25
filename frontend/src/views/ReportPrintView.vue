<template>
  <div class="print-page">
    <!-- Barra de acciones: solo en pantalla, no sale en el PDF -->
    <div class="print-toolbar no-print">
      <button class="print-btn" type="button" @click="doPrint" :disabled="!loaded">
        {{ $t('print.savePdf') }}
      </button>
      <span class="print-hint">{{ $t('print.hint') }}</span>
      <button class="print-btn ghost" type="button" @click="goBack">{{ $t('print.back') }}</button>
    </div>

    <p v-if="error" class="print-error no-print">{{ error }}</p>
    <p v-if="!loaded && !error" class="print-loading no-print">{{ $t('common.loading') }}</p>

    <!-- Documento con el estilo del sitio -->
    <article v-if="loaded" class="print-document">
      <header class="doc-header">
        <div class="doc-brand">
          <BrandMark />
        </div>
        <p class="doc-kicker">BeeMarketBot — {{ $t('print.reportLabel') }}</p>
        <h1 class="doc-title">{{ $t('print.title') }}</h1>
        <p class="doc-meta mono">
          {{ report.report_id }} · {{ $t('print.generatedAt') }} {{ formattedDate }}
        </p>
      </header>
      <div class="doc-body" v-html="renderMarkdown(report.markdown_content)"></div>
      <footer class="doc-footer">
        <span>BeeMarketBot</span>
        <span>{{ report.report_id }}</span>
      </footer>
    </article>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import BrandMark from '../components/BrandMark.vue'
import { getReport } from '../api/report'
import { renderMarkdown } from '../utils/markdown'

const props = defineProps({ reportId: String })

const router = useRouter()
const { t, locale } = useI18n()

const loaded = ref(false)
const error = ref('')
const report = ref(null)

const formattedDate = computed(() => {
  const value = report.value?.completed_at || report.value?.created_at
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString(locale.value)
  } catch {
    return value
  }
})

const doPrint = () => window.print()
const goBack = () => router.push({ name: 'Report', params: { reportId: props.reportId } })

onMounted(async () => {
  try {
    const res = await getReport(props.reportId)
    report.value = res.data
    loaded.value = true
    document.title = `${props.reportId} — BeeMarketBot`
    // El diálogo de impresión se abre solo: de ahí se elige "Guardar como PDF".
    setTimeout(() => window.print(), 600)
  } catch (err) {
    error.value = err.response?.data?.error || err.message
  }
})
</script>

<style scoped>
.print-page {
  min-height: 100vh;
  background: var(--w2-bg, #f5f0e8);
  padding: 24px 16px 60px;
}

.print-toolbar {
  display: flex;
  align-items: center;
  gap: 14px;
  max-width: 820px;
  margin: 0 auto 20px;
}

.print-btn {
  padding: 10px 18px;
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #fff;
  background: #1a2e4a;
  border: 2px solid #1a2e4a;
  border-radius: 6px;
  box-shadow: 4px 4px 0 rgba(26, 46, 74, 0.35);
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.print-btn:hover:not(:disabled) {
  transform: translate(-1px, -1px);
  box-shadow: 5px 5px 0 rgba(26, 46, 74, 0.35);
}

.print-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.print-btn.ghost {
  background: transparent;
  color: #cfd8e3;
  border-color: #cfd8e3;
}

.print-btn.ghost:hover:not(:disabled) {
  background: #cfd8e3;
  color: #1a2e4a;
}

.print-hint {
  font-size: 12px;
  color: #9aa7b8;
}

.print-loading, .print-error {
  max-width: 820px;
  margin: 40px auto;
  text-align: center;
}

.print-error { color: #c0392b; }

/* ---- El documento: papel claro con los acentos neobrutalistas del sitio ---- */
.print-document {
  max-width: 820px;
  margin: 0 auto;
  background: #fffdf7;
  border: 2px solid #1a2e4a;
  border-radius: 10px;
  box-shadow: 8px 8px 0 rgba(26, 46, 74, 0.25);
  padding: 48px 56px;
  color: #1a2e4a;
  font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
  line-height: 1.65;
}

.doc-header {
  border-bottom: 3px solid #1a2e4a;
  padding-bottom: 20px;
  margin-bottom: 28px;
}

.doc-brand { margin-bottom: 14px; }

.doc-kicker {
  margin: 0 0 6px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: #4a6fa5;
}

.doc-title {
  margin: 0 0 8px;
  font-size: 26px;
  line-height: 1.25;
}

.doc-meta {
  margin: 0;
  font-size: 12px;
  color: #666;
}

.doc-body :deep(h2),
.doc-body :deep(h3),
.doc-body :deep(h4),
.doc-body :deep(h5) {
  color: #1a2e4a;
  margin: 1.4em 0 0.5em;
  line-height: 1.3;
}

.doc-body :deep(h2) { font-size: 21px; border-bottom: 2px solid #1a2e4a; padding-bottom: 6px; }
.doc-body :deep(h3) { font-size: 17px; }
.doc-body :deep(h4) { font-size: 15px; }
.doc-body :deep(h5) { font-size: 13px; }

.doc-body :deep(.md-p) { margin: 0.7em 0; font-size: 13.5px; }

.doc-body :deep(blockquote),
.doc-body :deep(.md-quote) {
  margin: 1em 0;
  padding: 10px 16px;
  border-left: 4px solid #4a6fa5;
  background: rgba(74, 111, 165, 0.08);
  font-style: italic;
}

.doc-body :deep(pre),
.doc-body :deep(.code-block) {
  background: #1a2e4a;
  color: #f5f0e8;
  padding: 14px 16px;
  border-radius: 6px;
  font-size: 12px;
  overflow-x: auto;
}

.doc-body :deep(.inline-code),
.doc-body :deep(code) {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 0.92em;
}

.doc-body :deep(p code),
.doc-body :deep(li code) {
  background: rgba(26, 46, 74, 0.08);
  padding: 1px 5px;
  border-radius: 4px;
}

.doc-body :deep(ul),
.doc-body :deep(ol),
.doc-body :deep(.md-ul),
.doc-body :deep(.md-ol) {
  margin: 0.6em 0;
  padding-left: 1.6em;
}

.doc-body :deep(li) { margin: 0.3em 0; font-size: 13.5px; }

.doc-body :deep(.md-hr) {
  border: none;
  border-top: 2px solid rgba(26, 46, 74, 0.25);
  margin: 1.6em 0;
}

.doc-body :deep(strong) { color: #12305a; }

.doc-footer {
  margin-top: 36px;
  padding-top: 14px;
  border-top: 2px solid rgba(26, 46, 74, 0.25);
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #777;
}

/* ---- Impresión: solo el documento, con márgenes de página ---- */
@page {
  margin: 18mm 14mm;
}

@media print {
  .print-page { background: #fff; padding: 0; }
  .no-print { display: none !important; }
  .print-document {
    border: none;
    border-radius: 0;
    box-shadow: none;
    padding: 0;
    max-width: none;
    background: #fff;
  }
  .doc-body :deep(pre),
  .doc-body :deep(.code-block) {
    border: 1.5px solid #1a2e4a;
    background: #f2f4f8;
    color: #1a2e4a;
  }
}
</style>
