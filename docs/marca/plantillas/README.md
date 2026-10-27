# Plantillas · Sabor

| Archivo | Tamaño | Uso |
|---|---|---|
| `banner-readme-claro.svg/.png` | 1280×320 | Cabecera del README (tema claro) |
| `banner-readme-oscuro.svg/.png` | 1280×320 | Cabecera del README (tema oscuro) |
| `social-card-1280x640.svg/.png` | 1280×640 | Open Graph / «Social preview» de GitHub (Settings → General) |
| `post-cuadrado-1080.svg/.png` | 1080×1080 | Redes, formato cuadrado |
| `post-vertical-1080x1350.svg/.png` | 1080×1350 | Redes, formato vertical (ejemplo: menú semanal) |

**Edición:** el logotipo va en trazados (`<g id="logo">` o `id="firma"`); los textos son `<text>` editables (`<g id="copy">`), en Gloock y Figtree. Si editas en Figma o Inkscape, instala antes las dos fuentes. Para publicar, exporta a PNG; si el SVG va a un sitio sin las fuentes, pasa los textos a trazados.

Todo el copy está en español. Lema: «Lo que se cuece en tu casa.». Frase de apoyo: «Pregunta qué hay de comer. Tu IA ya lo tiene pensado.» (ver §1 del manual). No hay textos en inglés; «MCP» y «AGPL-3.0» son siglas.

## Cabecera del README con modo claro/oscuro

GitHub respeta `<picture>` con `prefers-color-scheme`. En el `README.md` de la raíz del repositorio las rutas son relativas a esa raíz:

```html
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/marca/plantillas/banner-readme-oscuro.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/marca/plantillas/banner-readme-claro.png">
    <img alt="Sabor · Lo que se cuece en tu casa. Pregunta qué hay de comer. Tu IA ya lo tiene pensado." src="docs/marca/plantillas/banner-readme-claro.png" width="100%">
  </picture>
</p>
```

En el README se usa el PNG, no el SVG: GitHub no carga web fonts dentro de un SVG enlazado y los textos se verían en otra letra.

## Favicon y meta (si hay web)
```html
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#7A1F35">
<meta property="og:image" content="/social-card-1280x640.png">
```
