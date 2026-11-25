<template>
  <div class="step-nav" role="navigation" :aria-label="$t('nav.steps')">
    <button
      v-for="step in steps"
      :key="step.num"
      type="button"
      class="step-chip"
      :class="{ current: step.num === current, done: step.reachable && step.num !== current }"
      :disabled="!step.reachable || step.num === current"
      :title="step.name"
      @click="go(step)"
    >
      <span class="chip-num">{{ step.num }}</span>
      <span v-if="step.num === current" class="chip-name">{{ step.name }}</span>
    </button>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getSimulation } from '../api/simulation'
import { getReport, getReportBySimulation } from '../api/report'

// Navegación entre los pasos del flujo. Cada paso carga su estado guardado en
// el servidor, así que moverse entre ellos no pierde nada: basta conocer los
// identificadores de la cadena proyecto → simulación → informe.

const props = defineProps({
  current: { type: Number, required: true },
  projectId: { type: String, default: '' },
  simulationId: { type: String, default: '' },
  reportId: { type: String, default: '' }
})

const router = useRouter()
const { tm } = useI18n()

// Lo que la vista actual conoce, más lo que se logra deducir del servidor.
const resolved = ref({
  projectId: props.projectId,
  simulationId: props.simulationId,
  reportId: props.reportId
})

onMounted(async () => {
  try {
    if (resolved.value.reportId && !resolved.value.simulationId) {
      const res = await getReport(resolved.value.reportId)
      if (res.success && res.data?.simulation_id) {
        resolved.value.simulationId = res.data.simulation_id
      }
    }
    if (resolved.value.simulationId && !resolved.value.projectId) {
      const res = await getSimulation(resolved.value.simulationId)
      if (res.success && res.data?.project_id) {
        resolved.value.projectId = res.data.project_id
      }
    }
    if (resolved.value.simulationId && !resolved.value.reportId) {
      // Todavía puede no existir informe: es normal, el paso 4 queda bloqueado.
      const res = await getReportBySimulation(resolved.value.simulationId)
      if (res.success && res.data?.report_id) {
        resolved.value.reportId = res.data.report_id
      }
    }
  } catch {
    // Sin cadena completa no hay navegación extra; los pasos conocidos bastan.
  }
})

const steps = computed(() => {
  const names = tm('main.stepNames')
  const ids = resolved.value
  // Qué identificador pide cada paso y a qué ruta lleva.
  const defs = [
    { num: 1, id: ids.projectId, route: { name: 'Process', params: { projectId: ids.projectId } } },
    { num: 2, id: ids.simulationId, route: { name: 'Simulation', params: { simulationId: ids.simulationId } } },
    { num: 3, id: ids.simulationId, route: { name: 'SimulationRun', params: { simulationId: ids.simulationId } } },
    { num: 4, id: ids.reportId, route: { name: 'Report', params: { reportId: ids.reportId } } },
    { num: 5, id: ids.reportId, route: { name: 'Interaction', params: { reportId: ids.reportId } } }
  ]
  return defs.map(def => ({
    ...def,
    name: names[def.num - 1] || `Paso ${def.num}`,
    reachable: Boolean(def.id)
  }))
})

const go = (step) => {
  if (!step.reachable || step.num === props.current) return
  router.push(step.route)
}
</script>

<style scoped>
.step-nav {
  display: flex;
  align-items: center;
  gap: 6px;
}

.step-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 8px;
  font: inherit;
  color: var(--w2-text-mute, #8a94a6);
  background: transparent;
  border: 1px solid rgb(var(--w2-text-rgb, 245 247 250) / 0.28);
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.step-chip .chip-num {
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.step-chip .chip-name {
  font-size: 12px;
  font-weight: 600;
}

.step-chip.done {
  color: var(--w2-text, #e8ecf4);
  border-color: var(--w2-accent, #4a6fa5);
}

.step-chip.done:hover {
  background: rgb(var(--w2-accent-rgb, 74 111 165) / 0.18);
}

.step-chip.current {
  color: var(--w2-on-accent, #fff);
  background: var(--w2-accent, #4a6fa5);
  border-color: var(--w2-accent, #4a6fa5);
  cursor: default;
}

.step-chip:disabled:not(.current) {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
