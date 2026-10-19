# Herramientas de Sabor

Servidor único: `sabor`, transporte Streamable HTTP, endpoint
`http://127.0.0.1:9170/mcp` desde el mismo host.
Descubre los esquemas de argumentos con tu integración MCP; no reutilices los
parámetros de los antiguos servidores independientes.

| Necesidad | Herramientas reales |
|---|---|
| Recetas | `search_recipes`, `get_recipe`, `create_recipe` |
| Búsqueda externa | `spoonacular_search_recipes`, `spoonacular_search_by_nutrients`, `spoonacular_nutrition_100g` |
| Web | `scrape_recipe` (preview), `import_recipe_to_mealie` (escritura) |
| Vídeo | `analyze_video_recipes`, `import_video_recipes_to_mealie` |
| Calendario | `get_mealplan_week`, `set_mealplan_entry`, `delete_mealplan_entry`, `clear_mealplan_week` |
| Compra | `get_shopping_lists`, `get_or_create_shopping_list`, `add_items_to_shopping_list`, `add_recipe_ingredients_to_list` |
| Bring! | `bring_get_lists`, `bring_sync_list`, `bring_add_items` |
| Precios | `mercadona_search_products`, `mercadona_get_product_price`, `mercadona_estimate_shopping_list_cost` |
| Memoria | `read_memory`, `save_feedback`, `update_pantry`, `update_learned_preference` |
| Historial | `add_recipe_comment`, `log_cooking_event`, `tag_recipe` |
| Nutrición | `nutrition_status`, `nutrition_recipe_readiness`, `nutrition_cookbook_readiness`, `nutrition_correct_recipe`, `nutrition_calculate_recipe`, `nutrition_publish_recipe` |

Nutrición está desactivada por defecto. Readiness funciona sin activarla.
`nutrition_correct_recipe` no escribe en Mealie ni siquiera con `apply=true`:
devuelve la propuesta y los pasos de edición externa. No cambies flags del
servidor desde la conversación. No inventes nutrición para saltar un bloqueo
de planificación.

Consulta el [inventario completo](../../README.md) para las 52 herramientas.
