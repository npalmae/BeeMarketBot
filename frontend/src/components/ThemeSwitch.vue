<template>
  <button
    class="theme-switch"
    type="button"
    :title="title"
    :aria-label="title"
    :aria-pressed="theme.light"
    @click="toggleTheme"
  >
    <!-- Sol en el modo claro, luna en el oscuro -->
    <svg v-if="theme.light" class="theme-icon" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4"></circle>
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"></path>
    </svg>
    <svg v-else class="theme-icon" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
    </svg>
    <span class="theme-label">{{ $t(theme.light ? 'theme.light' : 'theme.dark') }}</span>
  </button>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { theme, toggleTheme } from '../theme'

const { t } = useI18n()
// El botón dice en qué modo está; el aviso al pasar el puntero, a cuál cambia.
const title = computed(() => (theme.light ? t('theme.toDark') : t('theme.toLight')))
</script>

<style scoped>
/* Mismo aspecto que el selector de idioma bajo el tema Web2. */
.theme-switch {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid rgb(var(--w2-strong-rgb) / .5);
  background: transparent;
  color: var(--w2-text);
  font-family: 'DM Mono', monospace;
  font-size: 11px;
  letter-spacing: .08em;
  text-transform: uppercase;
  white-space: nowrap;
  cursor: pointer;
  transition: border-color .2s, background .2s, color .2s;
}

.theme-switch:hover,
.theme-switch:focus-visible {
  border-color: var(--w2-invert-bg);
  background: var(--w2-invert-bg);
  color: var(--w2-invert-text);
}

.theme-icon {
  flex: none;
  width: 12px;
  height: 12px;
}

/* En pantallas angostas la cabecera va justa: queda solo el icono. */
@media (max-width: 640px) {
  .theme-label { display: none; }

  .theme-switch { padding: 6px 8px; }
}
</style>
