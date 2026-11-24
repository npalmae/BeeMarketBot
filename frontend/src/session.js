// Token de la sesión, compartido entre el cliente HTTP y el estado de la sesión.
// Vive aparte para que el cliente HTTP no tenga que importar el estado (que lo importa a él).
export const sessionToken = { value: null }
