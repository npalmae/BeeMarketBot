<template>
  <div class="brand-lockup">
    <img class="brand-isotype" src="/beemarketbot-icon.png" alt="" width="30" height="30">
    <span>BEEMARKET<span class="brand-accent">BOT</span></span>
  </div>
</template>

<!--
  La marca de todas las cabeceras: el hexágono con la abeja (el mismo símbolo del
  icono de la pestaña) junto al nombre. Cada cabecera le pasa su propia clase
  (`brand`, `nav-brand`, `login-brand`), que conserva su tamaño y su estilo.

  Escrito directamente en el estilo Web2: está en la lista SKIP del generador del tema.
-->
<style scoped>
.brand-lockup {
  display: flex;
  align-items: center;
  gap: 10px;
}

.brand-isotype {
  display: block;
  flex: none;
  width: 30px;
  height: 30px;
}

/* El isotipo es decorativo para los lectores de pantalla: el nombre va justo al lado. */
.brand-accent { color: #76FA0A; }
</style>
