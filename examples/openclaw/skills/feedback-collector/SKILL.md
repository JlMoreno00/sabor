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

## Disponibilidad y resultados (0.2)

Antes de usar Sabor llama `sabor_capabilities()` y comprueba
`data.registered_tools`. Desenvuelve `data` cuando `ok=true`; con `ok=false`
revisa `error.code` y cualquier resultado parcial. Las herramientas de memoria
requieren selección explícita de `legacy_memory` en `SABOR_TOOLSETS` por el
operador; no cambies configuración desde el chat. Si no están disponibles,
explica la limitación sin afirmar que guardaste nada.

Para escrituras con preview: muestra → pregunta → confirma con `confirm=True`
o `dry_run=False`, según el esquema. Otros guardados también requieren permiso.
Para enviar compra a Bring!, construye primero una lista Mealie con
`add_to_shopping_list`, descubre destinos y previsualiza `sync_shopping_list`;
solo confirma después de aprobación. Nunca sincronices cadenas sueltas como
fuente alternativa a Mealie.
