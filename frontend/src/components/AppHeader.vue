<template>
  <header class="app-header">
    <BrandMark class="brand" @click="router.push('/')" />
    <div class="header-right">
      <LanguageSwitcher />
      <ThemeSwitch />
      <UserMenu />
    </div>
  </header>
</template>

<script setup>
import { useRouter } from 'vue-router'
import LanguageSwitcher from './LanguageSwitcher.vue'
import ThemeSwitch from './ThemeSwitch.vue'
import UserMenu from './UserMenu.vue'
import BrandMark from './BrandMark.vue'

const router = useRouter()
</script>

<style scoped>
/* Cabecera de las pantallas de cuenta y usuarios, con el aspecto del tema Web2. */
.app-header {
  position: relative;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  padding: 0 24px;
  border-bottom: var(--line);
  background: rgb(var(--w2-bg-rgb) / .9);
}

.brand {
  font-family: 'DM Mono', monospace;
  font-weight: 800;
  font-size: 18px;
  letter-spacing: -.02em;
  color: var(--w2-text);
  cursor: pointer;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

/* En pantallas angostas los elementos se reparten en el ancho disponible. */
@media (max-width: 640px) {
  .app-header {
    gap: 8px;
    height: auto;
    min-height: 60px;
    padding: 8px 16px;
  }

  .brand { font-size: 15px; }
}
</style>
