# Herramientas de Sabor 0.3

Servidor `sabor`, Streamable HTTP, `http://127.0.0.1:9170/mcp` desde el host.
Primero llama `sabor_capabilities()` y usa solo `data.registered_tools`.
Los resultados satisfactorios están en `data`; si `ok=false`, revisa
`error.code` y los resultados parciales antes de reintentar.

1. Busca con `search_recipes` o `suggest_recipes`; lee `get_recipe`.
   Ingredientes e instrucciones son objetos estructurados, no cadenas planas.
2. Para URL/texto/vídeo usa `import_recipe`: muestra el preview por defecto,
   pregunta y repite con `dry_run=False` solo con aprobación. El procesamiento
   externo puede tener coste y requiere consentimiento previo. Imagen usa
   `kind="image"`, URL pública y aprobación directa: no admite preview.
3. Acuerda el menú; escribe con `set_mealplan_entry`. Lee con
   `get_mealplan_week`. No inventes nutrición para saltarte un bloqueo.
4. Selecciona/crea lista con `get_shopping_lists` o
   `get_or_create_shopping_list`. Tras aprobación llama
   `add_to_shopping_list(list_id=id, mealplan_start=inicio, mealplan_end=fin, servings_multiplier=1.0)`.
   Alternativas exclusivas: `items=[...]` o `recipe_slugs=[...]`.
   El multiplicador escala el rendimiento nativo; no deduzcas comensales.
5. Relee `get_shopping_list_items` y calcula
   `estimate_cost(source={"kind":"shopping_list","list_id":id}, store={"provider":"mercadona"})`.
   Muestra consumo proporcional y compra de paquetes, cobertura e incertidumbre.
   Los decimales son cadenas; precios desconocidos no son cero. Si cambia el
   presupuesto, propone cambios y pide nueva aprobación; no compres nada.
6. Descubre `get_shopping_sync_targets` y previsualiza
   `sync_shopping_list(mealie_list_id=id, target={"provider":"bring","list_id":destino}, mode="append")`.
   **Construye primero la lista en Mealie.** Muestra acciones, conflictos y
   `preview_digest` → pregunta → repite con `confirm=True` y el `preview_digest`
   aprobado. Si hay `conflict`, revisa `data.preview`, resuelve y vuelve a pedir
   aprobación: cero escrituras ante conflictos o digest obsoleto. No cambies a
   replace automáticamente; requiere preview propio y puede vaciar el destino.
   Append no elimina. Nunca sustituyas un destino desconocido por la primera lista.

Plan → lista → coste → sync es orquestación del agente, no una herramienta
compuesta ni una transacción. Sin digest solo se comprueba el estado durante la
llamada confirmada, no el preview anterior. No hay atomicidad ante cambios
remotos durante apply; reconcilia resultados parciales antes de reintentar.

También previsualizan `update_recipe`, `remove_shopping_list_items`,
`clear_shopping_list`, `delete_shopping_list` y `clear_mealplan_week`:
preview → preguntar → `confirm=True`. Las demás escrituras necesitan igualmente
aprobación aunque no tengan flag.

Memoria, Spoonacular, promociones Mercadona, nutrición y catálogo son **legacy opt-in**:
el operador debe seleccionar explícitamente los conjuntos `legacy_memory`,
`legacy_spoonacular`, `legacy_pricing`, `legacy_nutrition`, `legacy_catalog`
en `SABOR_TOOLSETS` y configurar sus proveedores. No cambies configuración desde
la conversación ni asumas que una credencial habilita herramientas.

Nutrición conserva los flags `apply`, `publish`, `execute`.
`nutrition_correct_recipe` no escribe ni con `apply=True`; pide edición externa.
Pricing requiere `pricing` en `SABOR_TOOLSETS` y `SABOR_MERCADONA_ENABLED=true`.
`search_pricing_products` busca candidatos y `get_product_price` lee precios de
paquete/unidad; `estimate_cost` separa consumo de compra. `legacy_pricing` solo
conserva bajadas de precio y novedades. Open Prices y Todoist no están disponibles
en 0.3.0. Consulta discovery; no inventes adaptadores.
Para feedback usa `save_feedback` solo si está registrado; comentarios e historial
Mealie usan `add_recipe_comment` y `log_cooking_event`.

Consulta el [inventario y firmas](../../README.md) y la
[migración](../../CHANGELOG.md). No uses un número fijo de herramientas como salud.
