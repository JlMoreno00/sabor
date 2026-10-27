# Tipografía · Sabor

| Papel | Familia | Pesos | Uso |
|---|---|---|---|
| Titulares y display | **Gloock** | 400 (único peso) | Titulares, cifras destacadas, citas cortas. El logotipo está dibujado con Gloock y pasado a trazados. |
| Texto e interfaz | **Figtree** | 400 texto · 500 énfasis suave · 600 subtítulos y botones · 700 etiquetas cortas | Párrafos, UI, tablas, listas de la compra |

Ambas tienen licencia **SIL Open Font License 1.1**: se pueden usar, incrustar y redistribuir gratis.
- Gloock: https://fonts.google.com/specimen/Gloock
- Figtree: https://fonts.google.com/specimen/Figtree

## Escala (base 17 px, razón ≈ 1,33)

| Nivel | Tamaño | Familia | Interlineado |
|---|---|---|---|
| Display | 56 px · 3,5 rem | Gloock 400 | 1,05 |
| H1 | 40 px · 2,5 rem | Gloock 400 | 1,1 |
| H2 | 30 px · 1,875 rem | Figtree 600 | 1,2 |
| H3 | 22 px · 1,375 rem | Figtree 600 | 1,3 |
| Cuerpo | 17 px · 1,0625 rem | Figtree 400 | 1,55 |
| Pequeño | 14 px · 0,875 rem | Figtree 500 | 1,45 |
| Micro / etiquetas | 12 px · 0,75 rem | Figtree 600 o 700, versalitas con +0,12 em | 1,3 |

## Reglas
- Gloock solo a partir de 22 px: por debajo, su contraste de trazo se rompe.
- Nunca Gloock en párrafos largos ni en negrita sintética.
- Figtree en mayúsculas solo para etiquetas cortas (MENÚ, SEMANA 44), con tracking positivo.
- Cifras de cantidades (200 g, 4 raciones) en Figtree 500 con `font-variant-numeric: tabular-nums` en tablas.

## Alternativas del sistema (si no cargan las web fonts)
```css
--sabor-fuente-titular: Gloock, Georgia, "Times New Roman", serif;
--sabor-fuente-texto: Figtree, "Segoe UI", system-ui, -apple-system, Roboto, sans-serif;
```

Espécimen: `especimen.png` / `especimen.svg`.
