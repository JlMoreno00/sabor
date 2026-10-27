# Tono de ilustración y fotografía · Sabor

**Idea:** cocina de casa a fuego lento. Todo nace de dos piezas: la **«o»**, que es la cazuela o el plato visto desde arriba, y el **vapor**, que es la señal de que algo está hecho.

## Ilustración
- Formas planas, sin degradados ni sombras. En ilustración grande, trazo redondo (extremos y uniones redondeados). En los pictogramas, trazo con contraste y remates en gota, como Gloock.
- Paleta limitada: 2 o 3 colores de marca por pieza. El ocre se reserva para el vapor.
- El vapor siempre en S suave, en hilos de alturas distintas; nunca rizos apretados ni humo de cómic.
- Las cosas se ven desde arriba, de frente o de perfil, sin perspectiva.
- Nada de clichés: ni gorros de chef, ni cubiertos cruzados, ni hojas «healthy», ni chispas, robots, nodos o terminales.

## Fotografía (si llega a usarse)
- Luz natural lateral, cocina real y con uso: madera, barro, esmalte, paños. Nada de estudio blanco ni de bodegón de revista.
- Mejor manos y proceso (remojar, cortar, remover) que el plato emplatado. El vapor como protagonista cuando se pueda.
- Tratamiento cálido y ligeramente desaturado; las sombras hacia el negro tizón, nunca hacia el azul.
- Sin texto encima de la foto: el texto va en un bloque de color de la paleta.

## Patrón oficial: «Mercado»
Son los 13 pictogramas de `../pictogramas/`, sueltos, girados y a 50–64 px dentro de un tile sin costuras de 460 px.
- `patron-mercado-claro.svg/.png` (960×600) y `patron-mercado-claro-tile.svg`: rosa polvo y ocre muy suaves sobre cal. Para fondos, papelería y cabeceras.
- `patron-mercado-oscuro.svg/.png` (960×600) y `patron-mercado-oscuro-tile.svg`: granate y ocre apagados sobre pizarra, tono sobre tono. Para bloques oscuros.
- Va siempre de fondo. El logotipo se pone encima, sobre una placa del color del fondo o en una zona despejada. El patrón nunca se pone a más contraste que el de estos archivos.
- Para repetir el patrón, se usa `patron-mercado-claro-tile.svg` o `patron-mercado-oscuro-tile.svg` como `<pattern>` de 460×460, o escalado de forma proporcional.

## Pictogramas (`../pictogramas/`)
- 13 piezas en caja de 100: cazuela de barro, ajo, guindilla, granada, aceitunas, barra de pan, limón, huevo frito, cuchara de palo, tomate, alubias, romero y vapor.
- **Dibujo.** Formas llenas y planas, sin contorno. El trazo tiene contraste y los remates van en gota o en cuña, como Gloock. El vapor va en S suave y comparte forma con el del logotipo.
- **Color.** Sobre claro, granate y ocre de acento (por ejemplo `../pictogramas/ajo.svg`). Sobre oscuro, cal y ocre claro (el mismo nombre con sufijo `-cal`, por ejemplo `../pictogramas/ajo-cal.svg`). En patrones, tono sobre tono.
- **Tamaño mínimo: 32 px.** **El romero y las alubias no deben usarse a menos de 32 px.**
- No se mezclan con el icono de la app ni se pegan al logotipo.

## Ejemplos
- `ilustracion-cazuela.svg/.png`: la «o» como cazuela vista desde arriba, con cinco hilos de vapor (ocre en el centro, rosa en los bordes).
- El patrón anterior («o» humeante, claro y granate) está retirado.
