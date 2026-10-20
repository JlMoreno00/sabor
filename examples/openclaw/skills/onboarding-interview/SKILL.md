---
name: onboarding-interview
description: Entrevista inicial o actualización del perfil de cocina antes de planificar comidas.
---

# Entrevista de cocina

Activa cuando el usuario sea nuevo, falte un perfil o quiera actualizarlo.
Pregunta poco a poco; usa botones si están disponibles y texto libre si no.

1. Idioma, zona horaria y número de comensales; porciones diferentes si procede.
2. Alergias, intolerancias y restricciones. Aclara dudas; nunca deduzcas seguridad
   alimentaria a partir de una preferencia.
3. Días y comidas que desea planificar, incluidos fines de semana si quiere.
4. Presupuesto y tiendas disponibles; no presupongas Mercadona.
5. Tiempo diario, equipamiento y preferencia por cocinar fresco, sobras o batch.
6. Gustos, ingredientes rechazados y recetas favoritas.
7. Objetivos nutricionales solo si desea compartirlos, sin fijar metas médicas.
8. Si quiere recordatorios: horario, frecuencia y horas de silencio.

Resume el perfil y pide confirmación antes de guardarlo en el USER.md privado
del workspace. Si acepta guardar preferencias en Sabor, usa
`update_learned_preference` con un resumen fiel y mínimo. No existe una
herramienta de perfil estructurado: no inventes una. No guardes secretos.
Explica qué se guardó y permite corregirlo antes del primer menú.

## Disponibilidad y resultados (0.3)

Antes de usar Sabor llama `sabor_capabilities()` y comprueba
`data.registered_tools`. Desenvuelve `data` cuando `ok=true`; con `ok=false`
revisa `error.code` y cualquier resultado parcial. Las herramientas de memoria
requieren selección explícita de `legacy_memory` en `SABOR_TOOLSETS` por el
operador; no cambies configuración desde el chat. Si no están disponibles,
explica la limitación sin afirmar que guardaste nada.

Para escrituras con preview: muestra → pregunta → confirma con `confirm=True`
o `dry_run=False`, según el esquema. Otros guardados también requieren permiso.
Para enviar compra a Bring!, construye primero una lista Mealie aprobada con
`add_to_shopping_list` y relee los artículos. Si está registrado, llama
`estimate_cost(source={"kind":"shopping_list","list_id":id}, store={"provider":"mercadona"})`:
muestra consumo frente a paquetes a comprar, cobertura e incertidumbre. Los
precios ausentes no son cero; decimales como cadenas. Un cambio por presupuesto
requiere aprobación nueva. Pricing necesita `pricing` y el flag Mercadona; no
cambies configuración desde el chat ni confundas coste con autorización de compra.

Descubre destinos y previsualiza `sync_shopping_list`; muestra acciones,
conflictos y `preview_digest`, pregunta y confirma con `confirm=True` y el mismo
`preview_digest`. Si devuelve `conflict`, no reintentes ciegamente: revisa
`data.preview`, resuelve y pide nueva aprobación. Conflictos bloquean escrituras.
Replace requiere preview propio y puede vaciar el destino Bring. Sin digest no
se vincula al preview anterior. Reconcilia resultados parciales; no hay rollback
ni atomicidad entre sistemas. Nunca sincronices cadenas sueltas como alternativa
a la lista Mealie. Es orquestación del agente, no una herramienta compuesta.
