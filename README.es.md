[English](https://github.com/JlMoreno00/sabor/blob/main/README.md) · **Español**

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/JlMoreno00/sabor/main/docs/marca/plantillas/banner-readme-oscuro.png">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/JlMoreno00/sabor/main/docs/marca/plantillas/banner-readme-claro.png">
    <img alt="Sabor. Lo que se cuece en tu casa" src="https://raw.githubusercontent.com/JlMoreno00/sabor/main/docs/marca/plantillas/banner-readme-claro.png" width="100%">
  </picture>
</p>

# Sabor

Sabor ayuda a quienes cocinan en casa a usar su agente de IA para buscar o importar recetas, planificar la
semana, preparar una lista de la compra ajustada a las cantidades, estimar su coste y sincronizarla con Bring!.
Funciona de forma independiente con SQLite integrado por defecto; Mealie y Postgres/Supabase
son opcionales. La sincronización usa una vista previa explícita y una confirmación vinculada a un digest.

**Pídele a tu agente el menú de la semana y te devuelve la lista de la compra.**

Instala desde git (Python 3.12+ y uv):

```sh
uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp
```

Usa las skills portátiles [sabor-onboarding](https://github.com/JlMoreno00/sabor/blob/main/skills/sabor-onboarding/SKILL.md) y
[sabor-import](https://github.com/JlMoreno00/sabor/blob/main/skills/sabor-import/SKILL.md). Sabor expone **24 tools por
defecto, 53 con todos los toolsets** completamente configurados. Para guardar un perfil de cocina,
ejecuta `sabor-mcp setup` o incluye `preferences` en `SABOR_TOOLSETS`;
los registros directos con uvx y con un clic no lo activan. Consulta la
[configuración de toolsets](https://github.com/JlMoreno00/sabor/blob/main/docs/operations.md#toolset-and-provider-configuration-02).

Guía de marca: [docs/marca/](https://github.com/JlMoreno00/sabor/blob/main/docs/marca/manual-de-marca.md).

Los ajustes opcionales no secretos de `config.toml` siguen la prioridad entorno > archivo > valores por defecto.
Configura `SABOR_CONFIG` para seleccionar un archivo; consulta las [claves de configuración y rutas por sistema operativo](https://github.com/JlMoreno00/sabor/blob/main/docs/operations.md#optional-toml-configuration).
Los secretos pueden usar variables de entorno o referencias a archivos o al almacén de claves gestionadas por setup;
`sabor_capabilities` informa de `data.config_source`. Ejecuta `sabor-mcp doctor --json`
para comprobar el estado del almacén, o `sabor-mcp config show --json` para ver valores enmascarados y
sus orígenes. Consulta los [diagnósticos de terminal](https://github.com/JlMoreno00/sabor/blob/main/docs/operations.md#terminal-diagnostics).

## El flujo de cada día

1. Llama a `sabor_capabilities()`; usa solo las tools de `data.registered_tools`.
2. Busca recetas con `search_recipes` o `suggest_recipes`; consulta los ingredientes
   estructurados y las instrucciones con `get_recipe`. También puedes previsualizar
   `import_recipe(source="https://example.org/recipe", kind="url")`, preguntar al
   usuario y repetir con `dry_run=False`.
3. Acuerda un menú y usa `set_mealplan_entry` para cada entrada aprobada.
4. Crea o selecciona una lista de la compra del almacén y llama a
   `add_to_shopping_list(list_id="MEALIE_ID", mealplan_start="2026-10-19", mealplan_end="2026-10-25", servings_multiplier=2.0)`.
   Las fechas son inclusivas. Se añade cada aparición de una receta; las entradas de texto libre se
   omiten indicando los motivos. El multiplicador ajusta el rendimiento original de la receta, no un número
   estimado de comensales. Esta llamada escribe de inmediato: obtén aprobación primero.
5. Vuelve a leer `get_shopping_list_items` y estima la lista real con
   `estimate_cost(source={"kind":"shopping_list","list_id":"MEALIE_ID"}, store={"provider":"mercadona"})`.
   Muestra los subtotales de consumo y compra, la cobertura y las incertidumbres. Si
   supera el presupuesto, propón cambios y obtén una nueva aprobación antes de cambiar el plan.
6. Consulta `get_shopping_sync_targets`. Previsualiza
   `sync_shopping_list(mealie_list_id="MEALIE_ID", target={"provider":"bring","list_id":"BRING_ID"})`.
   Muestra las adiciones, eliminaciones, conflictos, elementos marcados omitidos y `preview_digest`.
   Pregunta y repite la misma llamada con `confirm=True, preview_digest="APPROVED_DIGEST"`.
   Un digest desactualizado o cualquier conflicto bloquea las escrituras y devuelve una nueva vista previa;
   revísala y vuelve a preguntar en vez de reintentar a ciegas o cambiar al modo de reemplazo.

Esto es **orquestación documentada del agente**, no una tool compuesta ni una transacción.
Plan → lista → estimate_cost → sync cruza límites de aprobación independientes.
La creación de listas y las adiciones escriben de inmediato; aprobar el coste no autoriza ninguna compra.
Informa del resultado de cada sistema; las escrituras inciertas requieren volver a leer y conciliar,
no reintentos automáticos ni dar por supuesta una reversión entre sistemas.

## Sabor solo frente a Sabor + Mealie

| Capacidad | Sabor solo | Sabor + Mealie |
|---|---|---|
| Recetas, planes de comidas, listas de la compra | SQLite integrado; sin cuenta ni token | URL de Mealie + token de API |
| Importación de URL | recipe-scrapers / JSON-LD sin conexión | Mismo extractor; guardado mediante la API CRUD de Mealie |
| Texto / imagen | El agente cliente extrae la receta y llama a `create_recipe` (sin proveedor de IA en Sabor) | Igual |
| URL de vídeo o archivo de vídeo local | `import_recipe` devuelve un paquete multimedia (metadatos, subtítulos, 8 fotogramas); el agente extrae la receta y llama a `create_recipe`. Archivos locales: solo stdio, dentro de `SABOR_MEDIA_ROOTS` (por defecto, el directorio personal). En stdio, el audio y los fotogramas también son archivos en `.sabor-media/` (se eliminan tras 24 h) | Igual |
| Sincronización con Bring! / estimaciones de Mercadona | Credenciales opcionales / activación voluntaria de precios | Mismas integraciones opcionales |
| Catálogo español / nutrición heredada | No disponible | Requiere el adaptador de Mealie |
| Libros de cocina | No disponibles | Libros de cocina de Mealie (`search_recipes(cookbook_id=...)`, tools de nutrición para libros de cocina) |
| Interfaz web para explorar y editar recetas | Ninguna: solo chat | Interfaz web de Mealie |

`sabor_capabilities` informa en `data.mealie_only` de los toolsets exclusivos de Mealie que no están disponibles en la
instancia actual, cada uno con un `reason`
(`requires_mealie_store` para `legacy_nutrition`, `requires_mealie` para
`legacy_catalog`) y un breve `detail`. ¿Ya usas Mealie? No tienes que
migrar nada: conserva `MEALIE_BASE_URL` y `MEALIE_API_TOKEN` y Sabor seguirá usándolo.
Instalaciones limpias paso a paso para SQLite, Mealie y Postgres/Supabase: [docs/install-beta.md](https://github.com/JlMoreno00/sabor/blob/main/docs/install-beta.md).

Inicializa el catálogo español de Mealie fuera de la lista de tools del agente:

```sh
sabor-mcp catalog status
sabor-mcp catalog apply
# Equivalent module entrypoints:
python -m sabor catalog status
python -m sabor catalog apply
```

Ambos requieren `MEALIE_BASE_URL` y `MEALIE_API_TOKEN`, independientemente de
`SABOR_TOOLSETS`. Status es una simulación de solo lectura; apply crea únicamente las
unidades seleccionadas que faltan y audita los alimentos. Ninguno actualiza, fusiona, elimina ni ejecuta seeders.
Los resultados son JSON UTF-8 con sangría; códigos de salida: 0 cuando `ok` es true, 1 por
fallo de configuración o conciliación, 2 por argumentos no válidos. Revisa los
`errors` y `conflicts` anidados incluso cuando `ok` sea true (semántica heredada).

## Capacidades y proveedores

| Capacidad | Proveedor / requisitos | Límites |
|---|---|---|
| Core | SQLite integrado, o URL de Mealie + token | Recetas, sugerencias, planificación, artículos de compra e historial; versión de referencia de la API: Mealie 3.22.0 |
| Importación: URL | recipe-scrapers sin conexión + schema.org JSON-LD para todos los almacenes; las recetas de Mealie se crean mediante su API CRUD, nunca sus scrapers | Vista previa de solo lectura; las páginas sin receta devuelven `not_found`; sin alternativa de IA |
| Importación: texto, imagen | Ninguno: Sabor no contiene ningún proveedor de IA | `import_recipe` devuelve `unsupported_operation`; el agente lee la fuente y la guarda con `create_recipe` |
| Importación: URL de vídeo | Un almacén de recetas; `ffmpeg` y `ffprobe` para fotogramas | Paquete multimedia para el agente: metadatos, subtítulos de la plataforma y 8 fotogramas como imágenes; no escribe ninguna receta (en stdio deja archivos de audio y fotogramas en `.sabor-media/`) |
| Sincronización de compra | Correo electrónico + contraseña de Bring! **y un almacén de recetas** | Solo elementos de origen sin marcar; no marca los elementos de Mealie |
| Precios | Toolset `pricing` + `SABOR_MERCADONA_ENABLED=true` o `SABOR_OPEN_PRICES_ENABLED=true` | Estimaciones de solo lectura según cantidades; catálogo de Mercadona u observaciones de Open Prices |
| Heredado | Selección explícita del toolset y configuración del proveedor | Promociones, memoria, nutrición y catálogo español |

Desde la versión 0.4.0, Sabor no contiene ningún cliente de proveedor de IA (#102): el agente cliente lee
texto, imágenes y vídeo por sí mismo y guarda la receta con `create_recipe`. Para una URL de vídeo,
`import_recipe` le entrega el título, autor, duración, fecha, plataforma
y descripción del vídeo, los subtítulos de la plataforma (primero el idioma original) y 8 fotogramas de
640 px como máximo, enviados como imágenes MCP; sin ffmpeg envía el resto y una
entrada `limitations`. Nunca escribe una receta, así que `dry_run` no tiene efecto. Los
ajustes `SABOR_LLM_*`, `GEMINI_API_KEY`, `VIDEO_RECIPE_GEMINI_MODEL` y `SABOR_VIDEO_PROVIDER`
se eliminaron: una configuración antigua sigue arrancando, estos se ignoran y
`sabor-mcp doctor` avisa una vez indicando sus nombres.

## Almacenes de recetas y límites de instancia

`RecipeStore` es una abstracción de persistencia tipada para recetas, entradas del calendario,
listas y artículos de compra, etiquetas e historial. Sin `MEALIE_BASE_URL`, se selecciona
SQLite automáticamente en `<data dir>/sabor.db`; configura `SABOR_STORE_DB` para cambiar la
ruta. Con una URL de Mealie no vacía, Mealie sigue seleccionado (y requiere su token).
Los valores explícitos `SABOR_STORE=sqlite`, `SABOR_STORE=mealie` o `SABOR_STORE=postgres` prevalecen sobre esa selección;
los valores no válidos impiden el arranque. La construcción y el registro de SQLite no realizan
operaciones de entrada/salida en el sistema de archivos. El arranque inicializa la base de datos antes de atender solicitudes;
el uso directo del almacén la inicializa en la primera operación. Se crean los directorios padre que falten
y los fallos de apertura nunca recurren silenciosamente a otra opción. Haz copias de seguridad de su volumen de datos.
La importación de URL funciona con SQLite y no necesita IA. El catálogo español y la nutrición
heredada siguen requiriendo Mealie.
Tandoor y KitchenOwl no son integraciones implementadas. Los argumentos existentes de slug y lista permanecen sin cambios,
incluido el argumento histórico de sincronización `mealie_list_id`. No hay ningún selector público
de URL de backend o tenant por llamada.

Para Postgres genérico (incluidos Supabase y Neon), instala el extra opcional
con `pip install 'sabor-mcp[postgres] @ git+https://github.com/JlMoreno00/sabor'` o ejecuta
`uvx --python ">=3.12" --from 'sabor-mcp[postgres] @ git+https://github.com/JlMoreno00/sabor' sabor-mcp`. Selecciona `SABOR_STORE=postgres` y
guarda el secreto `SABOR_DATABASE_URL` mediante el entorno o
`sabor-mcp setup secret database_url`. El adaptador usa el esquema privado `sabor`,
no requiere extensiones y activa RLS sin políticas públicas. SQLite y Mealie
no requieren el extra. Consulta [operaciones de Postgres / Supabase](https://github.com/JlMoreno00/sabor/blob/main/docs/operations.md#postgres--supabase-store)
para los permisos de roles, los ajustes del session pooler de Supabase y las copias de seguridad, y
[docs/supabase-agentes.md](https://github.com/JlMoreno00/sabor/blob/main/docs/supabase-agentes.md) para configurar Supabase con un agente
(`sabor-mcp setup supabase --create-role`).

Un `AppContext` vincula los ajustes, el almacén, el registro y los proveedores de precios a las
tools registradas. Las sesiones de Mealie, las cachés del catálogo de Mercadona y el estado de autenticación y caché
de Bring pertenecen a cada instancia. Cada tool registrada queda vinculada a la configuración del servidor;
los servicios de nutrición, las cachés y los bloqueos, las rutas de memoria y las sustituciones del catálogo de Bring están aislados entre registros. Esto
**no es un límite de seguridad multi-tenant**: el estado heredado de promociones y catálogo de Mercadona
y las variables globales de compatibilidad para llamadas directas siguen siendo compartidos por todo el proceso. Usa
procesos o contenedores, credenciales y volúmenes de datos separados para hogares no confiables;
los registros aislados deben usar rutas distintas para las bases de datos del almacén de recetas, memoria y nutrición.
Las llamadas directas de compatibilidad en Python no equivalen a contextos de aplicación registrados.

El descubrimiento expone `data.capabilities.recipe_store` y `pricing` con los ID de adaptador
y las operaciones; `data.mealie.configured` sigue siendo un campo de compatibilidad obsoleto.
El descubrimiento de importación enumera el importador `sabor` con `url` (vista previa/confirmación) y `video` (paquete).
Configurado significa configurado localmente, nunca autenticado ni en buen estado.

## Precios según cantidades

Activa `SABOR_MERCADONA_ENABLED=true` **y añade `pricing` a `SABOR_TOOLSETS`**
(por ejemplo, `core,import,shopping_sync,pricing`). Los valores predeterminados no cambian.
`search_pricing_products` busca candidatos; `get_product_price` devuelve precios por envase
y por unidad normalizada, base de cantidad, fechas, ubicación, procedencia e
incertidumbre. `estimate_cost` solo lee; nunca modifica recetas ni compra productos.
`legacy_pricing` ahora solo expone bajadas de precio y novedades.

La búsqueda de Mercadona solo incluye alimentos (se excluyen las categorías principales
no alimentarias 20, 21, 22, 23, 25 y 26). La coincidencia es por palabras completas
y tolera una `s`/`es` final en cualquiera de los lados (`zanahoria` encuentra
`Zanahorias`; `sal` nunca encuentra `Salsa`). Un número que forma parte del nombre
(`4 quesos`, no una cantidad como `500 g`, `2 huevos` o una fracción como `1/2 kg`)
prioriza los productos que lo contienen para mantenerlos en la ventana de 50 resultados.
Un número escrito a partir de dos cuenta como sus dígitos (`cuatro quesos` como
`4 quesos`); un artículo (`un queso`) siempre es una cantidad. Los artículos de compra
introducidos como texto libre en un almacén integrado se comparan con su texto original,
por lo que `4 quesos` conserva su número; los artículos de Mealie conservan su selección
anterior (Beta A). El número debe modificar la misma palabra: `Pizza 4 quesos` contiene
`4 quesos`, `Pizza 3 quesos pack 4 unidades` no, y `estimate_cost` aplica la misma regla.
Después, las coincidencias se ordenan por palabras no solicitadas que indiquen procesado
o composición (`croquetas`, `chicles`, `salsa`, `molido`, `a la jardinera`, leche condensada...),
luego por el número de palabras normalizadas del nombre ausentes de la consulta (menos es
más cercano), metadatos alimentarios, nombre e ID. Los modificadores solicitados no se
penalizan (`croquetas de merluza` encuentra croquetas), y entera, desnatada y semidesnatada
permanecen neutrales. Esto mide cercanía léxica, no identidad semántica de ingredientes.
`estimate_cost(..., match_policy="cheapest")` selecciona por defecto el candidato alimentario
compatible (ventana de búsqueda de hasta 50) con el menor coste de compra para la cantidad
necesaria, es decir, envases completos por precio del envase, y después el menor precio por
unidad. Para 200 g de harina elige una bolsa de 1 kg en lugar de una de 5 kg más barata por
kilo. Varios candidatos generan `estimated` / `auto_selected_cheapest` y hasta cinco
`alternatives` con nombres, precios normalizados y `checkout_cost`. Un ingrediente básico
ignora candidatos procesados o compuestos cuando coincide uno sin procesar (`merluza` elige
filetes en lugar de croquetas más baratas, `jengibre` fresco en lugar de chicles); solo se
usan cuando no hay otra coincidencia. Muestra las alternativas y ofrece cambiarlas mediante
`product_overrides`; las selecciones manuales tienen prioridad, pero no pueden calcular el
precio de productos no alimentarios. Cuando al menos dos líneas sin selección manual comparten
palabras normalizadas del nombre y unidad base, también se evalúan juntas las cantidades
conocidas. Un producto común sustituye las elecciones por línea solo si su coste de compra
conjunto es estrictamente menor que el aprovisionamiento agrupado original; en caso de empate
se conservan las elecciones originales y el coste de la cesta nunca aumenta. Para 300 g +
300 g de arroz, 500 g a EUR 0.60 pierde frente a 1 kg a EUR 0.90 (EUR 1.20 → 0.90).
Para 200 g + 900 g con 1 kg a EUR 1.00, se mantienen las elecciones mixtas (EUR 1.60,
no EUR 1.80). Las líneas modificadas añaden evidencia `auto_selected_cheapest_group` y
`grouped_selection`; sus alternativas muestran costes de compra para la cantidad sumada
(hasta cinco). Usa `match_policy="strict"` para dejar sin valorar las ambigüedades; el modo
estricto no agrupa. Una cantidad positiva sin unidad presupone piezas (`estimated`,
`assumed_count`), que solo coinciden con productos contados por unidades. Las medidas de
cocina siguen siendo desconocidas. Los precios de referencia de huevos por docena se dividen
entre 12 para obtener la base por pieza.

`store={"provider":"mercadona"}` es un **contexto comercial**, no un backend de recetas.
Los valores opcionales `location_id` y `currency` deben coincidir con el adaptador configurado
(configuración predeterminada: almacén `mad1`, EUR); los contextos incompatibles se rechazan,
nunca se cambian silenciosamente. Omite `store` solo cuando haya exactamente un proveedor de
precios. La API no oficial de Mercadona puede cambiar. La sincronización con Todoist está
prevista, pero aún no se incluye.

También puedes activar `SABOR_OPEN_PRICES_ENABLED=true` (por defecto `false`) y el toolset
`pricing`. Selecciona `store={"provider":"open_prices","location_id":"ES","currency":"EUR"}`
con `estimate_cost` o `get_product_price`; los ID de producto son códigos de barras. El país
es un código ISO alpha-2 (por defecto ES) y la moneda predeterminada es EUR. Open Prices recoge
observaciones colaborativas de todo el mundo, no es un catálogo actual: la cobertura española
es escasa y antigua. Prevalece la observación coincidente más reciente del país (en caso de
empate, el ID más reciente), nunca la más barata. Las ubicaciones u observaciones del país
que falten devuelven `not_found`, no cero. La búsqueda devuelve hasta diez productos con
observaciones; `search_pricing_products` omite los que no tienen un precio en el país
solicitado en lugar de hacer fallar toda la búsqueda, e intenta completar el límite solicitado
con los siguientes candidatos con precio dentro de la ventana que devuelve el proveedor
(Open Prices examina como máximo diez), por lo que pueden llegar menos resultados
(`get_product_price` sobre uno de esos productos sigue devolviendo `not_found`). El filtro de
nombre busca subcadenas, así que una consulta plural de una palabra que devuelve menos de diez
resultados también busca su singular para completar el espacio restante (`guisantes` también
encuentra `Guisante fino`); los resultados originales siempre van primero y las consultas de
varias palabras se envían tal cual.
Las cotizaciones incluyen siempre la fecha de observación, tienda/ciudad públicas, disponibilidad
desconocida y `observation`; `stale_observation` indica más de 180 días, `discounted` marca
promociones, `single_observation` significa que solo hay una observación coincidente en la
ventana recuperada y `unknown_unit` deja desconocidos los precios por envase/unidad.
Las respuestas se almacenan en caché por instancia durante diez minutos (hasta 128 solicitudes);
los precios solo examinan las 50 observaciones más recientes, por lo que la cobertura por país
puede ser incompleta. Atribución: **Open Prices (Open Food Facts), ODbL**,
https://prices.openfoodfacts.org/. No combines estos datos con datos no libres en una base de
datos redistribuida. Las cotizaciones y los errores nunca incluyen identificadores de
colaboradores, pruebas ni coordenadas.

Elige exactamente una estructura de `source`:

```json
{"kind":"items","items":[{"name":"arroz","quantity":"500","unit":"g","text":"500 g arroz"}]}
{"kind":"recipe","slug":"arroz-con-verduras","multiplier":"1"}
{"kind":"shopping_list","list_id":"MEALIE_ID"}
{"kind":"mealplan","start_date":"2026-10-19","end_date":"2026-10-25","multiplier":"1"}
```

Los artículos en línea funcionan sin Mealie; las referencias almacenadas requieren un almacén
de recetas. Usa cadenas decimales o enteros para cantidades/multiplicadores, **no números de
coma flotante JSON**. Límites: 500 filas resueltas, 31 días incluidos, multiplicador mayor que
cero y como máximo 20. Las comidas repetidas siguen siendo ocurrencias separadas; las entradas
solo de texto quedan sin valorar. No se infiere automáticamente el número de raciones ni se
resta lo que hay en la despensa.

- `consumption_subtotal`: cantidad necesaria × precio por unidad comparable.
- `procurement_subtotal`: envases completos que comprar, redondeando hacia arriba **después de
  agregar** la demanda del mismo producto/ubicación/moneda. Para 500 g de un envase de 1 kg
  cuyo precio es EUR 2, el consumo es EUR 1 y la compra EUR 2.
- Los operandos decimales y el dinero son **cadenas** JSON; los subtotales se redondean a dos
  decimales solo para su presentación. Los precios ausentes nunca son cero; las estimaciones
  vacías o totalmente desconocidas tienen subtotales nulos. Los subtotales parciales no son
  presupuestos completos.
- Revisa `complete`, `coverage`, `excluded_rows`, `uncertainty.level` de cada línea
  (`known`, `estimated`, `unknown`) y los motivos. La densidad, el peso por pieza, las unidades,
  las coincidencias ambiguas o el tamaño del envase desconocidos quedan sin resolver. La
  disponibilidad y las observaciones antiguas siguen siendo incertidumbres, no garantías de compra.
- `product_overrides` asigna los valores `key` de las líneas devueltas a los ID de producto
  seleccionados; revisa primero los candidatos. No hay alternativa entre minoristas ni total con
  monedas mezcladas. Compara `source_digest` para saber si ha cambiado la fuente resuelta
  (claves de línea, nombres, cantidades necesarias y unidades); ignora las actualizaciones de
  precios. `quote_digest` también incluye las observaciones de precios y cambia con cada
  actualización del catálogo. Ninguno sustituye al `preview_digest` de la sincronización.

## Elige qué tools se exponen

`SABOR_TOOLSETS` tiene por defecto el valor `core,import,shopping_sync`. Es una lista de
permitidos que se cruza con la configuración disponible. Vacío significa solo descubrimiento;
se eliminan los espacios exteriores, se agrupan los duplicados y los nombres desconocidos impiden
el arranque.

| Toolset | Selección / configuración |
|---|---|
| `core` | Por defecto; SQLite integrado o URL + token de Mealie |
| `import` | Por defecto; un almacén de recetas; solo páginas web de recetas (`kind='url'`) |
| `shopping_sync` | Por defecto; credenciales de Bring! + almacén de recetas para las tools |
| `pricing` | Activación opcional + `SABOR_MERCADONA_ENABLED=true` o `SABOR_OPEN_PRICES_ENABLED=true`; no requiere Mealie para artículos en línea |
| `legacy_spoonacular` | Activación opcional + `SPOONACULAR_API_KEY` |
| `legacy_pricing` | Activación opcional + `SABOR_MERCADONA_ENABLED=true` |
| `legacy_memory` | Activación opcional; `MEMORY_FILE` local |
| `legacy_nutrition` | Activación opcional; las operaciones conservan `SABOR_NUTRITION_ENABLED` y los controles existentes |
| `legacy_catalog` | Activación opcional + Mealie |

El arranque predeterminado con SQLite registra **24 tools**: 22 tools de core,
`import_recipe` y `sabor_capabilities`. Con todos los toolsets completamente configurados,
hay **53 tools**: 52 tools de aplicación seleccionables más la de descubrimiento.
Los recuentos no son una comprobación de salud.

`sabor_capabilities()` informa de la versión, los toolsets solicitados, los nombres realmente
registrados, las capacidades/adaptadores configurados, los tipos/operaciones compatibles, los
motivos de desactivación y las advertencias. Es descubrimiento local, no una comprobación de
salud de la red. Omite credenciales, URL privadas, rutas, direcciones de correo e ID de modelo.
Reinicia y actualiza el descubrimiento del cliente después de cambiar la configuración.

## Resultados y aprobación

Extrae los resultados correctos de `data`:

```json
{"ok":true,"data":{"preview":true,"actions":[]}}
```

Los fallos usan una estructura sanitizada (`data` opcional contiene resultados parciales seguros):

```json
{"ok":false,"error":{"code":"provider_unconfigured","message":"Recipe store is not configured.","provider":"mealie"}}
```

Códigos: `invalid_argument`, `provider_unconfigured`, `unsupported_operation`,
`not_found`, `ambiguous_target`, `authentication_failed`, `rate_limited`,
`timeout`, `upstream_error`, `invalid_response`, `conflict`, `partial_failure`.
La validación del esquema MCP puede rechazar una llamada antes de ejecutar una tool.

- `confirm=False` previsualiza actualizaciones de recetas, eliminación de artículos de compra,
  vaciado/eliminación de listas, vaciado de rangos del calendario y ambos modos de sincronización.
  Muestra la vista previa → pregunta → confirma.
- La importación usa `dry_run=True` por defecto; la vista previa obtiene la página de la receta,
  pero no crea recetas. Sabor no llama por sí mismo a ningún proveedor de IA. Con Mealie, una
  importación confirmada ejecuta el analizador de ingredientes de Mealie (`SABOR_INGREDIENT_PARSER`,
  `nlp` local por defecto); solo `openai` envía el texto de los ingredientes al proveedor de IA
  configurado en Mealie. Sabor nunca usa los scrapers de Mealie, cuya alternativa es el proveedor
  de IA de Mealie.
- Otras escrituras requieren aprobación en el cliente incluso sin una bandera del servidor.
  Los controles heredados `apply`, `publish` y `execute` conservan sus significados independientes.
- La sincronización acepta el `preview_digest` opcional. Al proporcionar el digest de la vista
  previa aprobada, la confirmación queda vinculada a esa fuente, destino, modo, acciones y alcance
  de aplicación; una discrepancia devuelve `conflict` con una nueva vista previa en `data.preview`
  y ninguna escritura. Cualquier conflicto no vacío bloquea la confirmación aunque el digest
  coincida. Sin digest, el comportamiento de compatibilidad solo vuelve a comprobar las instantáneas
  actuales dentro de la llamada confirmada; no se vincula a la vista previa anterior del usuario.
  Ninguno de los modos hace atómicas las ediciones remotas durante la aplicación. Revisa los
  resultados parciales antes de reintentar.
- Añadir no elimina entradas del destino; las especificaciones diferentes son conflictos.
  Reemplazar puede vaciar todas las entradas de compra del destino, incluso con una fuente vacía.
  Los destinos desconocidos o ambiguos fallan: no se recurre a la primera lista.
- Los arrays PATCH de recetas reemplazan arrays completos. Revisa las eliminaciones, no solo
  las adiciones.

## Instalación

### 1. Con tu IA (recomendado)

Pega esto en tu agente de programación (Claude Code, Cursor, Codex, Gemini CLI, Cline, OpenClaw…):

```text
Install and configure Sabor by following
https://github.com/JlMoreno00/sabor/blob/main/llms-install.md
```

En Cursor, [abre este prompt en el chat](https://cursor.com/link/prompt?text=Install%20and%20configure%20Sabor%20by%20following%20https%3A%2F%2Fgithub.com%2FJlMoreno00%2Fsabor%2Fblob%2Fmain%2Fllms-install.md)
(solo rellena el chat; no se ejecuta nada hasta que lo envíes). El agente hace unas pocas
preguntas de una en una, configura Sabor con `sabor-mcp setup` en una terminal y verifica la
configuración y el estado del almacén con `sabor-mcp doctor --json`. Esto no comprueba el
transporte MCP ni las credenciales de los proveedores; completa la verificación con
`sabor_capabilities` y la prueba completa de ida y vuelta de [llms-install.md](https://github.com/JlMoreno00/sabor/blob/main/llms-install.md).
Tú introduces los secretos en una terminal (`sabor-mcp setup secret <name>`), nunca en el chat.
Consulta [llms-install.md](https://github.com/JlMoreno00/sabor/blob/main/llms-install.md).

### 2. Con un clic (SQLite local, sin preguntas)

Estos enlaces registran la instalación desde git con la versión de Python fijada. Después de
publicar la versión 0.4.0 en PyPI, también estará disponible `uvx --python ">=3.12" sabor-mcp`.

- [Añadir a Cursor](https://cursor.com/en/install-mcp?name=sabor&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLXB5dGhvbiIsIj49My4xMiIsIi0tZnJvbSIsImdpdCtodHRwczovL2dpdGh1Yi5jb20vSmxNb3Jlbm8wMC9zYWJvciIsInNhYm9yLW1jcCJdfQ==)
- [Instalar en VS Code](https://insiders.vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522sabor%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522--python%2522%252C%2522%253E%253D3.12%2522%252C%2522--from%2522%252C%2522git%252Bhttps%253A%252F%252Fgithub.com%252FJlMoreno00%252Fsabor%2522%252C%2522sabor-mcp%2522%255D%257D)
  ([Insiders](https://insiders.vscode.dev/redirect?url=vscode-insiders%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522sabor%2522%252C%2522command%2522%253A%2522uvx%2522%252C%2522args%2522%253A%255B%2522--python%2522%252C%2522%253E%253D3.12%2522%252C%2522--from%2522%252C%2522git%252Bhttps%253A%252F%252Fgithub.com%252FJlMoreno00%252Fsabor%2522%252C%2522sabor-mcp%2522%255D%257D))
- [Añadir a Kiro](https://kiro.dev/launch/mcp/add?name=sabor&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--python%22%2C%22%3E%3D3.12%22%2C%22--from%22%2C%22git%2Bhttps%3A%2F%2Fgithub.com%2FJlMoreno00%2Fsabor%22%2C%22sabor-mcp%22%5D%7D)
- [Añadir a LM Studio](https://lmstudio.ai/install-mcp?name=sabor&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLXB5dGhvbiIsIj49My4xMiIsIi0tZnJvbSIsImdpdCtodHRwczovL2dpdGh1Yi5jb20vSmxNb3Jlbm8wMC9zYWJvciIsInNhYm9yLW1jcCJdfQ==)
- [Instalar en Goose](https://block.github.io/goose/extension?cmd=uvx&arg=--python&arg=%3E%3D3.12&arg=--from&arg=git%2Bhttps%3A%2F%2Fgithub.com%2FJlMoreno00%2Fsabor&arg=sabor-mcp&id=sabor&name=Sabor&description=Recipes%2C%20meal%20plans%20and%20shopping%20lists)

O desde una terminal:

```bash
claude mcp add --scope user sabor -- uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp
codex mcp add sabor -- uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp
gemini mcp add --scope user sabor uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp
code --add-mcp '{"name":"sabor","command":"uvx","args":["--python",">=3.12","--from","git+https://github.com/JlMoreno00/sabor","sabor-mcp"]}'
```

Sin un `config.toml`, Sabor arranca con SQLite local en el directorio de datos del sistema
operativo; ejecuta `sabor-mcp setup` más adelante para añadir Mealie, precios o Bring!.

### 3. Docker o Mealie (avanzado)

Consulta [Inicio rápido: Docker — Sabor + Mealie](#inicio-rápido-docker--sabor--mealie) y
[docs/install-beta.md](https://github.com/JlMoreno00/sabor/blob/main/docs/install-beta.md).

## Inicio rápido: Sabor solo

Con Python 3.12+ y [uv](https://docs.astral.sh/uv/), la instalación stdio es:

```bash
uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp
```

Después de publicar la versión 0.4.0 en PyPI, usa `uvx --python ">=3.12" sabor-mcp`, o
construye un wheel local con `uv build` y ejecuta
`uvx --from dist/sabor_mcp-0.4.0-py3-none-any.whl sabor-mcp`.
No se necesita configuración de Mealie. El comando de consola usa stdio por defecto;
`sabor-mcp --transport http` sirve Streamable HTTP en loopback (`127.0.0.1:8080/mcp`)
salvo que configures `SABOR_HOST` explícitamente.

Para Docker independiente (HTTP), **solo después de la primera publicación en GHCR**:

```bash
docker run -p 127.0.0.1:9170:8080 -v sabor-data:/data ghcr.io/jlmoreno00/sabor:0.4.0
```

Hasta entonces, construye la imagen localmente con `docker build -t sabor:local .` y usa
`sabor:local` en ese comando. No se necesitan variables de Mealie.
Conecta a `http://127.0.0.1:9170/mcp` (el puerto 9170 del host corresponde al puerto 8080
del contenedor). La imagen oficial no incluye el extra `[postgres]`; consulta la receta de la
[variante de imagen Postgres](https://github.com/JlMoreno00/sabor/blob/main/docs/install-beta.md#c-postgres--supabase).

Los datos se guardan en `SABOR_DATA_DIR` si está configurado; si no, en un directorio `/data`
existente (Docker) y, en caso contrario, en el directorio de datos del usuario del sistema operativo:
`$XDG_DATA_HOME/sabor` o `~/.local/share/sabor` en Linux,
`~/Library/Application Support/sabor` en macOS, `%LOCALAPPDATA%\\sabor` en Windows.
Las rutas de SQLite, memoria, nutrición y caché de catálogo se derivan de él; las opciones
explícitas por archivo siguen teniendo prioridad. Persiste el volumen de Docker y haz copias de
seguridad de los datos locales.

Los fotogramas de vídeo en el paquete multimedia del agente necesitan **ffmpeg / ffprobe** del
sistema (incluidos en Docker); instala ffmpeg por separado para uvx. Sin ellos, `import_recipe`
sigue devolviendo los metadatos y subtítulos del vídeo, con la limitación `no_frames`.

## Inicio rápido: Docker — Sabor + Mealie

Necesitas Docker con Compose v2. Nada más: el perfil `mealie` también inicia
una instancia de Mealie para Sabor (fijada a la versión probada 3.22.0).

1. **Configura.** Desde el repositorio ejecuta `cp .env.example .env` y establece
   `MEALIE_BASE_URL=http://mealie:9000` en `.env`.
2. **Inicia y conecta Mealie.** Ejecuta `docker compose --profile mealie up -d`,
   abre Mealie en `http://localhost:9925`, inicia sesión como administrador y
   crea un token de API (perfil > API Tokens > Generate). Pégalo en
   `MEALIE_API_TOKEN` dentro de `.env`.
3. **Aplica los cambios y conecta tu agente.** Ejecuta
   `docker compose --profile mealie up -d --force-recreate sabor` y registra
   `http://127.0.0.1:9170/mcp` en tu cliente MCP
   ([Conecta tu cliente MCP](#conecta-tu-cliente-mcp)).

Añade `COMPOSE_PROFILES=mealie` a `.env` para omitir `--profile mealie` en
comandos posteriores. ¿Ya tienes Mealie en ejecución? Omite el perfil y apunta
`MEALIE_BASE_URL` a esa instancia, por ejemplo `http://host.docker.internal:9925`
para Mealie en el host Docker; entonces `docker compose up -d` inicia solo Sabor.
Los datos persisten en los volúmenes `sabor_data` y `mealie_data`, y ambos
servicios publican solo en loopback (`127.0.0.1:9170` y `127.0.0.1:9925`;
cambia el puerto de Sabor con `SABOR_HOST_PORT` y el de Mealie en
`docker-compose.override.yml`). Sabor se compila localmente hasta que se
publique una imagen de lanzamiento en `ghcr.io/jlmoreno00/sabor`.

Comprueba el transporte sin ejecutar una tool de aplicación:

```bash
curl -fsS -X POST \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"healthcheck","version":"1.0.0"}},"id":1}' \
  http://127.0.0.1:9170/mcp
```

Una respuesta de initialize comprueba el servidor y el transporte, no las credenciales del proveedor.

### Sin Docker

Usa Python 3.12 o superior. El procesamiento local de vídeo también necesita `ffmpeg` (incluido `ffprobe`). Para vídeos
sin subtítulos, el extra opcional `sabor-mcp[transcribe]` transcribe el audio
localmente en la CPU (faster-whisper; sin proveedor de IA, consulta `docs/operations.md`).

```bash
python3 -m venv .venv
. .venv/bin/activate
# Optional extras: pip install -e '.[postgres]' or pip install -e '.[transcribe]'
pip install -e .
export SABOR_HOST=127.0.0.1
export SABOR_PORT=9170
export SABOR_DATA_DIR="$PWD/.local-data"
sabor-mcp --transport http # or: python -m sabor --transport http
```

Mantén `.local-data` fuera del control de versiones. Estas variables de entorno configuran
el mismo endpoint que Docker; sin ellas, la CLI HTTP escucha en `127.0.0.1:8080`.
La nutrición permanece desactivada. No pegues tokens reales en historiales de shell compartidos.

## Conecta tu cliente MCP

Para stdio, deja que el cliente MCP ejecute la instalación desde git con uvx:

```json
{
  "mcpServers": {
    "sabor": {
      "command": "uvx",
      "args": ["--python", ">=3.12", "--from", "git+https://github.com/JlMoreno00/sabor", "sabor-mcp", "--transport", "stdio"]
    }
  }
}
```

Reserva stdout para el protocolo; los logs van a stderr.

Para Docker o un proceso HTTP en ejecución, registra un servidor Streamable HTTP llamado
`sabor`. Una configuración genérica es:

```json
{
  "mcpServers": {
    "sabor": {
      "type": "http",
      "url": "http://127.0.0.1:9170/mcp"
    }
  }
}
```

Los nombres de los campos de configuración dependen de tu cliente. Para Claude Code:

```bash
claude mcp add --transport http sabor http://127.0.0.1:9170/mcp
```

Loopback solo funciona cuando el cliente se ejecuta en el host Docker. Para un cliente
en un contenedor o remoto, configura deliberadamente la conectividad privada; no expongas
este endpoint públicamente sin una capa de acceso autenticado.


## Referencia de tools por toolset

Las firmas siguientes se generan a partir de funciones Python decoradas; `None` significa
opcional/no establecido. Se requieren IDs estables, no nombres de lista aproximados. Usa el descubrimiento MCP
para consultar los esquemas completos y la disponibilidad actual.

### Siempre activas

```python
sabor_capabilities()
```

### core

`search_recipes` acepta `sort="name"` (predeterminado), `created_at`, `updated_at`,
`rating`, `last_made` o `random`, con `direction="asc"` o `"desc"`.
Random ignora la dirección y usa la fecha UTC (`YYYY-MM-DD`) como semilla
diaria, manteniendo estable la paginación durante el día si no cambian las recetas ni los filtros.
Las recetas sin valoración o nunca preparadas aparecen al final en ambas direcciones. SQLite no tiene
valoraciones: `sort="rating"` devuelve `unsupported_operation`, no un orden alternativo.


```python
add_recipe_comment(slug: str, text: str)
log_cooking_event(slug: str, subject: str, message: str | None=None)
get_mealplan_week(start_date: str, end_date: str)
set_mealplan_entry(date_str: str, entry_type: str, title: str, recipe_slug: str | None=None)
delete_mealplan_entry(entry_id: str)
clear_mealplan_week(start_date: str, end_date: str, confirm: bool=False)
search_recipes(query: str='', page: int=1, per_page: int=25, tag_ids: list[str] | None=None, category_ids: list[str] | None=None, food_ids: list[str] | None=None, tool_ids: list[str] | None=None, cookbook_id: str | None=None, require_all: bool=False, sort: str='name', direction: str='asc')
get_recipe(slug: str)
update_recipe(slug: str, patch: dict, confirm: bool=False)
suggest_recipes(foods: list[str], tools: list[str] | None=None, max_missing_foods: int=2, max_missing_tools: int=0, limit: int=10, include_foods_on_hand: bool=True)
create_recipe(name: str, description: str | None=None, recipe_yield: str | None=None, total_time: str | None=None, ingredients: list[str] | None=None, instructions: list[str] | None=None, tags: list[str] | None=None, source_url: str | None=None)
get_shopping_lists()
create_shopping_list(name: str)
delete_shopping_list(list_id: str, confirm: bool=False)
clear_shopping_list(list_id: str, confirm: bool=False)
get_or_create_shopping_list(name: str)
add_to_shopping_list(list_id: str, items: list[str] | None=None, recipe_slugs: list[str] | None=None, mealplan_start: str | None=None, mealplan_end: str | None=None, servings_multiplier: float=1.0)
get_shopping_list_items(list_id: str, checked: bool | None=None, page: int=1, per_page: int=100)
check_shopping_list_items(list_id: str, item_ids: list[str], checked: bool=True)
remove_shopping_list_items(list_id: str, item_ids: list[str], confirm: bool=False)
get_or_create_tag(name: str)
tag_recipe(slug: str, tags: list[str])
```

### import

```python
import_recipe(source: str, kind: str='auto', dry_run: bool=True, language: str='en', max_recipes: int=8, min_confidence: float=0.55)
```

### shopping_sync

```python
get_shopping_sync_targets(provider: str | None=None)
sync_shopping_list(mealie_list_id: str, target: dict, mode: str='append', confirm: bool=False, preview_digest: str | None=None)
```

### Estimaciones según la despensa

La lista `pantry` del perfil predeterminado de las [preferencias de instancia](#preferences) se lee
bajo el bloqueo de preferencias, incluso cuando el toolset `preferences` está desactivado.
Si faltan archivos, la despensa se considera vacía. Un archivo corrupto hace que las estimaciones
con `exclude_pantry: true` fallen con un `invalid_response` sanitizado; las estimaciones sin esa opción
nunca leen las preferencias. Un archivo corrupto no impide añadir a la lista de compra (consulta
`pantry_warning` más abajo). Aquí no se modifican datos de la despensa.

Cada tipo de fuente de `estimate_cost` acepta el parámetro opcional `exclude_pantry: bool`:
`estimate_cost(source={"kind":"recipe","slug":"rice","exclude_pantry":True})`.
Si se omite o es false, la respuesta completa no cambia y no se leen las preferencias.
Si es true, se omite el cálculo del precio de las líneas coincidentes, se mantienen en `lines` y `excluded_rows` con
`uncertainty.reasons: ["in_pantry"]`, se cuentan en `excluded` de la cobertura y se
añade `pantry: {"applied": true, "matched": N}`. Los subtotales excluyen esas líneas;
si no se calcula el coste de ninguna, los subtotales siguen siendo null, no cero.

La comparación usa nombres de alimentos estructurados o `ingredient_name` en texto libre y después
aplica conversión de mayúsculas y minúsculas, elimina acentos y normaliza espacios. `ingredient_name` elimina
una cantidad inicial, una unidad en español o inglés (`300 g de arroz` y `300 grams of
rice` dan `arroz` y `rice`) y el conector `de`/`of`. Las unidades en inglés se aplican
con cualquier almacén, incluido Mealie; las líneas en español no cambian. Los nombres completos deben coincidir,
o coincidir después de quitar una `s` o `es` final en cualquiera de los dos lados. No hay coincidencia aproximada ni
de subcadenas: `huevo` coincide con `huevos`, pero `sal` nunca coincide con `salsa`.
Es una comparación léxica, no una contabilidad de existencias según cantidades.

`add_to_shopping_list` siempre añade todas las líneas solicitadas. Si la despensa no está vacía,
sus datos de éxito también contienen `pantry_matches`, por ejemplo
`[{"name":"huevos","pantry_entry":"huevo"}]` (o `[]` si no hay coincidencias).
El campo no aparece si la despensa está vacía o no existe. Las indicaciones de despensa nunca bloquean
la adición: si no se pueden leer las preferencias o falla la comparación, las líneas se añaden igualmente
y la respuesta incluye `pantry_warning`. SQLite, Mealie y Fake usan
las líneas de origen: texto de artículos en línea o ingredientes de receta de cada aparición, leídos
antes de escribir. Los IDs se omiten deliberadamente porque los almacenes pueden fusionar líneas;
vuelve a leer `get_shopping_list_items` para identificar los IDs, pide aprobación y usa
`remove_shopping_list_items` si lo deseas. No se elimina ni omite nada automáticamente.

### pricing

```python
search_pricing_products(query: str, store: dict | None=None, limit: int=10)
get_product_price(product_id: str, store: dict | None=None)
estimate_cost(source: dict, store: dict | None=None, product_overrides: dict | None=None, match_policy: str='cheapest')
```

### legacy_spoonacular

```python
spoonacular_search_recipes(query: str='', number: int=5, max_calories: float | None=None, min_calories: float | None=None, max_protein_g: float | None=None, min_protein_g: float | None=None, max_fat_g: float | None=None, min_fat_g: float | None=None, max_carbs_g: float | None=None, min_carbs_g: float | None=None)
spoonacular_nutrition_100g(ingredient: str)
```

### legacy_pricing

```python
mercadona_get_price_drops()
mercadona_get_new_arrivals()
```

### preferences

Actívalo con `SABOR_TOOLSETS=core,import,shopping_sync,preferences`; los valores
predeterminados no cambian. Estas tools no necesitan proveedor. Las preferencias son por instancia /
hogar, no por usuario autenticado; solo se usa el perfil predeterminado reservado.
Ambas son decisiones provisionales pendientes de confirmación de los mantenedores: el toolset sigue siendo
opcional y los perfiles por usuario esperan a que haya usuarios autenticados (#28).
Previsualiza primero los cambios y, tras la aprobación, repite con `confirm=True`.

```python
get_preferences()
update_preferences(changes: dict, confirm: bool=False)
```

`changes` acepta `set` (sustituir campos), `add` y `remove` (solo campos de lista).
Campos: `diet`, `dislikes`, `pantry`, `notes` son listas de hasta 200 cadenas;
las cadenas se recortan, no pueden estar vacías, tienen como máximo 200 caracteres y se deduplican sin distinguir
mayúsculas de minúsculas. `household_size` es null o un entero entre 1 y 20. Las operaciones se ejecutan en
orden set → add → remove. Las claves desconocidas o los valores inválidos no escriben nada.
Las respuestas incluyen preferencias, updated_at y source (`defaults`, `file` o `postgres`);
las actualizaciones también incluyen las diferencias de campos antes/después y el indicador de previsualización. La previsualización
no cambia la marca de tiempo. La despensa también está disponible para precios y creación de listas
sin activar este toolset; consulta [estimaciones según la despensa](#estimaciones-según-la-despensa).

### legacy_memory

Obsoleto en favor de `preferences`. Estas tools siguen usando MEMORY.md sin cambios,
incluido `save_feedback`; las valoraciones, favoritos y comentarios no se redirigen.
Consulta las [instrucciones de migración](https://github.com/JlMoreno00/sabor/blob/main/docs/operations.md#structured-preferences-migration).

```python
save_feedback(recipe_name: str, rating: int | None=None, comment: str | None=None, tags: list[str] | None=None)
read_memory(section: str | None=None)
update_pantry(items: list[str])
update_learned_preference(preference: str)
```

### legacy_nutrition

```python
nutrition_recipe_readiness(slug: str, include_suggestions: bool=True)
nutrition_correct_recipe(slug: str, expected_revision: dict[str, str | None], corrections: list[dict[str, Any]], apply: bool=False)
nutrition_status()
nutrition_food_profile(mealie_food_id: str)
nutrition_set_food_profile(mealie_food_id: str, calories_kcal: float | None=None, protein_g: float | None=None, carbohydrate_g: float | None=None, net_carbohydrate_g: float | None=None, fat_g: float | None=None, saturated_fat_g: float | None=None, unsaturated_fat_g: float | None=None, trans_fat_g: float | None=None, fiber_g: float | None=None, sugar_g: float | None=None, sodium_mg: float | None=None, cholesterol_mg: float | None=None, basis_grams: float=100, confidence: float | None=None, source_note: str='', source_version: str='', apply: bool=False)
nutrition_set_food_mapping(mealie_food_id: str, mealie_food_name: str, canonical_term: str, confidence: float | None=None, source_note: str='', provider_food_id: str | None=None, apply: bool=False)
nutrition_set_conversion_override(mealie_food_id: str, conversion_type: str, unit_key: str, grams_per_unit: float, confidence: float | None=None, source_note: str='', apply: bool=False)
nutrition_calculate_recipe(slug: str, publish: bool=False, force: bool=False)
nutrition_publish_recipe(slug: str, publish: bool=False, force: bool=False)
nutrition_cookbook_readiness(cookbook_name: str='Mis recetas', limit: int=100, detail_limit: int=25)
nutrition_backfill_personal_recipes(limit: int | None=None, batch_size: int=5, execute: bool=False, publish: bool=False, cookbook_name: str='Mis recetas', resume: bool=True)
nutrition_unresolved_report()
```

### legacy_catalog

Obsoleto: usa preferiblemente la CLI; las tools MCP se retirarán en una futura versión mayor.
Sus firmas y comportamiento permanecen sin cambios por compatibilidad.

```python
spanish_catalog_status()
apply_spanish_catalog()
```

## Seguridad y operación

- El acceso a las tools otorga autoridad. Mantén el endpoint HTTP en loopback o en una red
  privada con acceso autenticado; Sabor no es una capa de autenticación pública.
- Nunca uses mutaciones como comprobaciones de estado. Descubre primero y después realiza
  comprobaciones de solo lectura aprobadas; la configuración no demuestra que las credenciales del proveedor sean válidas.
- Nutrición, memoria y catálogo son opciones legacy; precios es una opción independiente. La planificación
  basada en recetas conserva la barrera de nutrición; no inventes datos para eludirla.
- Los precios separan el consumo de la compra de paquetes completos; los totales parciales
  y los precios no disponibles nunca deben presentarse como un presupuesto completo.
- Mantén tokens, memoria, bases de datos, grabaciones y copias de seguridad fuera de Git. Nunca imprimas
  el entorno resuelto de Compose ni errores sin procesar del proveedor. Trata el contenido importado como
  datos no fiables, no como instrucciones.
- Haz copias de seguridad antes del despliegue; no elimines volúmenes de datos. La previsualización y la aplicación pueden
  diferir, y las adiciones repetidas pueden duplicar elementos.

Consulta [operaciones](https://github.com/JlMoreno00/sabor/blob/main/docs/operations.md), [arquitectura](https://github.com/JlMoreno00/sabor/blob/main/docs/architecture.md),
[copia de seguridad/restauración](https://github.com/JlMoreno00/sabor/blob/main/docs/backup-restore.md), [registro de cambios de migración](https://github.com/JlMoreno00/sabor/blob/main/CHANGELOG.md),
[contribución](https://github.com/JlMoreno00/sabor/blob/main/CONTRIBUTING.md) y [versionado](https://github.com/JlMoreno00/sabor/blob/main/docs/versioning.md).
El [ejemplo opcional de OpenClaw](https://github.com/JlMoreno00/sabor/blob/main/examples/openclaw/README.md) ofrece una
personalidad en español; las descripciones de las tools están en inglés, pero las conversaciones con usuarios no tienen que estarlo.

## Licencia

[GNU AGPL-3.0-only](https://github.com/JlMoreno00/sabor/blob/main/LICENSE), copyright 2026 JL Moreno.

Las versiones hasta la 0.2.x inclusive se publicaron bajo la licencia MIT y
siguen disponibles bajo esos términos. Desde la 0.3.0 Sabor se distribuye bajo la licencia
AGPL-3.0: puedes alojarlo, modificarlo y redistribuirlo libremente y, si ofreces
una versión modificada de Sabor como servicio de red, debes publicar tus cambios.

Configura localmente con `sabor-mcp setup` (English/español), o usa
`sabor-mcp setup --non-interactive --json`. Consulta [Configuración](https://github.com/JlMoreno00/sabor/blob/main/docs/operations.md#setup).
