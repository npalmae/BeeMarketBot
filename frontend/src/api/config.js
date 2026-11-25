import service from './index'

// Conexión con los servicios de IA (sección Modelos, solo administradores).

/** La configuración vigente: claves enmascaradas y origen de cada campo. */
export const getModelsConfig = () => {
  return service.get('/api/config/models')
}

/**
 * Guarda la configuración. En cada campo: texto = valor nuevo, "" = heredar
 * del servidor; para las claves, null = conservar la guardada.
 * @param {Object} data - { llm?: {...}, embedding?: {...} }
 */
export const saveModelsConfig = (data) => {
  return service.put('/api/config/models', data)
}

/**
 * Prueba la conexión con un servicio sin guardar nada.
 * @param {Object} data - { kind: 'llm'|'embedding', config?: {...} }
 */
export const testModelsConnection = (data) => {
  return service.post('/api/config/models/test', data)
}
