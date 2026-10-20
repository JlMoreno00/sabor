---
name: feedback-collector
description: Recoger valoraciones y comentarios después de cocinar y aprender preferencias con consentimiento.
---

# Feedback

Activa al recibir una opinión sobre una receta o cuando el usuario haya
acordado una solicitud de feedback.

1. Identifica la receta; si hay ambigüedad, pregunta antes de guardar.
2. Acepta valoración de 1 a 5, comentario y preferencias como favorita,
   repetir pronto o no repetir. No fuerces una valoración.
3. Confirma qué se guardará y llama `save_feedback` según su esquema real.
4. Si el usuario quiere una nota visible en Mealie, resuelve el slug con
   `search_recipes` y publica `add_recipe_comment`. Para registrar que se cocinó,
   usa `log_cooking_event` solo tras confirmación.
5. Usa `update_learned_preference` para preferencias explícitas; pregunta antes
   de generalizar una sola opinión a toda una categoría.

Si falla una escritura, informa qué parte se guardó y cuál no. No afirmes
sincronización completa ni repitas automáticamente un comentario duplicable.
Nunca publiques el perfil privado en un chat grupal.

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
