# Contrast: every Dartec variant, both modes

WCAG 2.x contrast ratios, computed from [dartec-variants.yaml](dartec-variants.yaml) on 2026-09-25.

**The bar:**

- text, secondary text, text on the page and the top bar's text: at least **4.5:1** (AA)
- the accent, the AC mode colours and "light on", which are controls and icons: at least **3:1**

**How cards are measured:**

- Opaque cards are measured directly.
- **Translucent (glass) cards are blended over every colour stop of the view's background gradient**, and the lowest ratio is reported. It is the worst place on the screen a card can sit.
- Variables a variant does not set fall back to Home Assistant's current defaults.

**Result: 12 of 12 pass.** A variant that fails does not ship.

| Variant | Mode | text on card | secondary text on card | text on page | header text | accent on card | AC cool on card | AC dry on card | AC fan on card | light on on card | AA |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Dartec | light | 15.27 | 7.39 | 14.2 | n/a | 6.15 | 6.15 | 4.98 | 9.17 | 3.46 | pass |
| Dartec | dark | 13.06 | 6.75 | 14.23 | n/a | 6.49 | 6.49 | 8.22 | 9.14 | 10.65 | pass |
| Dartec Glass | light | 14.8 | 7.63 | 12.89 | 12.9 | 5.67 | 5.67 | 4.59 | 8.46 | 3.19 | pass |
| Dartec Glass | dark | 7.56 | 6.09 | 9.23 | 12.96 | 5.3 | 5.3 | 5.67 | 6.6 | 6.54 | pass |
| Dartec Glass Lite | light | 15.32 | 7.9 | 12.89 | 12.9 | 5.87 | 5.87 | 4.75 | 8.75 | 3.3 | pass |
| Dartec Glass Lite | dark | 12.0 | 8.05 | 9.23 | 12.96 | 6.88 | 6.88 | 7.9 | 9.28 | 8.93 | pass |
| Dartec Soft | light | 15.27 | 7.39 | 13.6 | n/a | 6.15 | 6.15 | 4.98 | 9.17 | 3.46 | pass |
| Dartec Soft | dark | 12.29 | 6.81 | 14.23 | n/a | 6.11 | 6.11 | 7.73 | 8.6 | 10.02 | pass |
| Dartec Material | light | 13.0 | 6.45 | 14.34 | 11.05 | 5.54 | 5.54 | 5.27 | 5.49 | 3.04 | pass |
| Dartec Material | dark | 12.09 | 7.05 | 14.45 | 13.07 | 8.89 | 8.89 | 8.76 | 12.41 | 9.9 | pass |
| Dartec Graphite | light | 15.05 | 6.89 | 13.39 | 13.39 | 5.77 | 5.77 | 5.26 | 8.61 | 3.25 | pass |
| Dartec Graphite | dark | 13.28 | 6.81 | 14.48 | 14.48 | 7.29 | 7.29 | 8.0 | 10.04 | 9.25 | pass |

The first Glass draft failed in dark mode: secondary text 3.91 and accent 3.34 where the card sat over the lightest teal of the gradient. The ground was deepened and the dark-mode text and accents lightened; the table is the corrected version.

Live check: the pixel-identical render of the installed Dartec theme v1.1.0 against the in-page method (see [README.md](README.md#how-these-were-tested)) means these figures are what the browser draws. For comparison, the live figures for the first guide's themes are in [../themes.md](../themes.md#contrast-measured-live).
