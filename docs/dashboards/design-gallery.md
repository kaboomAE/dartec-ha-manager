# Design gallery: how the showcase dashboards get their look

The owner asked how to get dashboards that look like the screenshots in eight
popular theme repositories. Then he asked for **Dartec's own variants**
inspired by them, rather than adopting any of the eight. This page is the
analysis: what actually produces each look. The Dartec designs are in
[dartec-variants/](dartec-variants/).

**Method.** We read each repository's README, theme files and issues at a
pinned commit, and looked at every screenshot: 105 images in the first four
repositories, and the full sets in the other four. For each look we estimated
how much comes from each of these:

- the **theme's variables**, which is all a theme can safely carry;
- **card-mod or UIX CSS**, which is JavaScript injecting styles;
- **custom cards**;
- **layout**;
- **background image**;
- **blur**.

Original screenshots are linked from their own repositories at the pinned
commit. **None is copied into this repository.**

## The short answer

**Most of what makes these screenshots attractive is not the theme.** Across
the eight, the theme's own variables account for between 15% and 85% of the
look. The rest comes from four places:

- **Photographs behind the cards.** Two sets are Apple's wallpapers, which
  nobody may ship.
- **Custom cards**: clock-and-weather cards, graphs, media players, navigation
  bars.
- **card-mod or UIX CSS**, which runs code and breaks on Home Assistant updates.
- **A deliberate layout.**

The one exception is **frosted glass**. Home Assistant has supported blurred,
translucent cards through plain theme variables since 2024.5:

- `ha-card-background`
- `ha-card-backdrop-filter`
- `ha-card-box-shadow`
- `ha-card-border-*`

We confirmed this in the frontend source (`src/components/ha-card.ts`), and
proved it on the bench. So the glass look Dartec wants **needs no card-mod**.

| Look | Repository (pinned) | Theme variables | card-mod / UIX | Custom cards | Background image | Layout | Blur | Verdict for customers |
|---|---|---|---|---|---|---|---|---|
| Frosted Glass | [wessamlauf/homeassistant-frosted-glass-themes](https://github.com/wessamlauf/homeassistant-frosted-glass-themes) @ `8cb9503` (MIT, v1.3) | 30% | 25% (required) | 15% | 15% (6000 × 4000 photo from a CDN branch) | 15% | ~45 blurred regions per screen | **Inspiration only.** Recreated as Dartec Glass with no card-mod |
| visionOS / Liquid Glass | [Nezz/homeassistant-visionos-theme](https://github.com/Nezz/homeassistant-visionos-theme) @ `fe49637` (MIT, 3.0.7) | ~50% (glass comes from built-in variables) | small (sidebar, dropdown fix) | clock-weather-card | ~35% (Apple macOS wallpapers, hot-linked) | — | one blur on every card, the header and the sidebar | **Inspiration only.** Open issue about lag since 2026.8 (#66); wallpapers are Apple's |
| iOS Themes | [basnijholt/lovelace-ios-themes](https://github.com/basnijholt/lovelace-ios-themes) @ `8c21d38` (MIT, v3.0.3) | 30% | 0 (5% in the author's dashboard) | 30% (mini-graph, mini-media-player, button-card, vacuum map) | 25% (Apple HomeKit wallpapers) | 10% | none | **Do not install.** v3.0.3 themes vanish from the picker (#110). Screenshots are from 2020 |
| HA-LCARS | [th3jesta/ha-lcars](https://github.com/th3jesta/ha-lcars) @ `0c4ce18` (MIT; fan-production disclaimer) | 15% | 55% (92 KB of CSS via UIX) | 5% (lcars.js) | — | 25% | none | **Not for customers.** Personal-use disclaimer, ~235 hard-coded left/right rules (breaks right-to-left), fonts with no Arabic, broke on five HA releases in 2025–2026 |
| Catppuccin | [catppuccin/home-assistant](https://github.com/catppuccin/home-assistant) @ `b37ada0` (MIT, v2.1.3) | 85% | 0 | 0 | 0 | 15% | none | **Usable as-is.** Its screenshots are HA's own demo on built-in cards |
| Material You | [Nerwyn/material-you-theme](https://github.com/Nerwyn/material-you-theme) @ `3cee02a` (Apache-2.0, 5.0.15) | ~35% | 0 | author uses several | 0 | — | none | **Theme alone is fine** (60–65% of the look). The rest is `material-you-utilities`, 267 KB of JavaScript that has needed a fix for almost every HA minor release |
| Graphite | [TilmanGriesel/graphite](https://github.com/TilmanGriesel/graphite) @ `1e8ab5a` (MIT, 2.7.6) | ~55% | 0 | clock-weather style card (inferred) | 0 | — | none | **Inspiration.** Light accent fails contrast (2.38:1). No commits since June |
| Noctis | [aFFekopp/noctis](https://github.com/aFFekopp/noctis) @ `5515804` (**no licence**, 3.3.5) | ~40% | 0 (the documented block is gone) | ~40% (button-card, mini-graph, bar-card) | 0 | masonry, 2020 | one, dialog backdrop only | **Look only.** No licence, so nothing may be reused. Screenshots are from January 2020 |

## Each look, in more detail

### Frosted Glass and visionOS: the glass look

**What produces it:**

- Nearly transparent cards: Frosted Glass's card-mod takes them to about 10%
  opacity, while visionOS uses 30%.
- A strong blur behind each card (8–20 px).
- Large radii (20–34 px).
- Inset highlight shadows.
- A photograph, which does most of the work.

**What breaks it:**

- **Contrast depends on the photo.** At 10% opacity, text sits almost directly
  on the photograph, so no contrast figure can be promised.
- **Blur is expensive.** On the bench, blur multiplied the drawing work during
  a scroll by about 11 times (see
  [dartec-variants/README.md](dartec-variants/README.md#cost-on-a-cheap-wall-tablet)).
  visionOS has an open issue about severe lag since HA 2026.8.
- **The photographs are the problem, not the idea.** One is a 6000 × 4000 image
  (about 96 MB decoded) loaded at runtime from a CDN branch that can change.
  Others are Apple's wallpapers, which may not be shipped.
- **Frosted Glass requires card-mod.** Its users fix "glass not applied" by
  switching to UIX.

**Dartec's version:** [Dartec Glass](dartec-variants/README.md#dartec-glass).

- The same built-in variables, and no card-mod.
- A brand gradient instead of a photograph, so every text pair is checked
  against every colour in the gradient.
- Cards opaque enough to guarantee contrast.
- A no-blur fallback, **Dartec Glass Lite**.

| Frosted Glass (original) | visionOS (original) | Dartec Glass |
|---|---|---|
| ![Frosted Glass, dark](https://github.com/user-attachments/assets/8fadd748-c3a9-4578-994d-838f2b6a1329) | ![visionOS, dark](https://github.com/user-attachments/assets/273f0e86-180e-42b3-abe0-bab25c359584) | ![Dartec Glass, dark](dartec-variants/screenshots/dartec-glass-room-tablet-dark-en.webp) |
| ![Frosted Glass, light](https://github.com/user-attachments/assets/6adca904-9a3d-4df6-b080-1480fce3fa35) | ![visionOS, light](https://github.com/user-attachments/assets/c60d760b-4531-41c2-b8b5-47404e8743d7) | ![Dartec Glass, light](dartec-variants/screenshots/dartec-glass-room-tablet-light-en.webp) |

### iOS Themes: the soft look

**What produces it:**

- iOS system colours.
- 40% translucent cards with a 20 px radius.
- A wallpaper.
- In the screenshots, a set of custom cards (mini-graph, mini-media-player,
  button-card) in a 2020 masonry layout.

**What breaks it:**

- The current release makes every theme disappear from the picker (#110).
- The wallpapers are Apple's HomeKit artwork.

**Dartec's version:** [Dartec Soft](dartec-variants/README.md#dartec-soft).

- Borderless paper cards with a soft, two-layer shadow on Dartec's cream.
- 12 px corners. This is a deliberate, small step up from the brand's 8 px,
  and it is the only concession to the iOS softness.
- No wallpaper and no translucency.

| iOS Themes (original) | Dartec Soft |
|---|---|
| ![iOS Themes overview](https://raw.githubusercontent.com/basnijholt/lovelace-ios-themes/8c21d38f55cb07b193e42afeaf417db5cd42c247/docs/theme-overview.jpg) | ![Dartec Soft, light](dartec-variants/screenshots/dartec-soft-room-tablet-light-en.webp) |
| ![iOS Themes, dark](https://raw.githubusercontent.com/basnijholt/lovelace-ios-themes/24299541778c6bec7f6931035bc60463dd67d2d7/screenshots/blue-red-dark.png) | ![Dartec Soft, dark](dartec-variants/screenshots/dartec-soft-room-tablet-dark-en.webp) |

### Material You: the tonal look

**What produces it:**

- A tonal palette in which surfaces, buttons and accents are all shades of one
  colour.
- Large radii and pill-shaped controls.

Of that, the theme file gives about 60–65%. The rest (the bottom navigation
bar, large headlines and colours generated at runtime) is the
`material-you-utilities` JavaScript module, and the author's screenshots all
use it.

**Dartec's version:** [Dartec Material](dartec-variants/README.md#dartec-material).

- The Material 3 "tonal spot" scheme generated from Dartec teal #1A6B6B, with
  the dark ground lifted off near-black to follow the brand.
- 16 px cards.
- No JavaScript.

| Material You (original) | Dartec Material |
|---|---|
| ![Material You, light](https://raw.githubusercontent.com/Nerwyn/material-you-theme/3cee02a49353583100d8cb7936a26d290f77d0b9/assets/material-you-wide-light.png) | ![Dartec Material, light](dartec-variants/screenshots/dartec-material-room-tablet-light-en.webp) |
| ![Material You, dark](https://raw.githubusercontent.com/Nerwyn/material-you-theme/3cee02a49353583100d8cb7936a26d290f77d0b9/assets/material-you-wide-dark.png) | ![Dartec Material, dark](dartec-variants/screenshots/dartec-material-room-tablet-dark-en.webp) |

### Graphite and Noctis: the quiet dark look

**What produces it:**

- Near-neutral surfaces with a single accent, a system font and flat cards.
- In Graphite's case, a 20 px radius and orange.
- In Noctis's case, navy and blue, with graphs and big buttons from custom
  cards.

Both are cheap to draw and safe in Arabic.

**What breaks it:**

- Graphite's light accent fails contrast, and its font issue (#132) was closed
  without a fix.
- Noctis has no licence, and its screenshots show a 2020 dashboard.

**Dartec's version:** [Dartec Graphite](dartec-variants/README.md#dartec-graphite).

- Warm graphite neutrals.
- Teal only where something is on.
- 8 px corners, as the brand specifies.
- Grey icons when off, so a bedroom panel at night shows light only where
  something is running.

| Graphite (original) | Noctis (original, 2020) | Dartec Graphite |
|---|---|---|
| ![Graphite, dark](https://raw.githubusercontent.com/TilmanGriesel/graphite/1e8ab5a5d64a724a8d5c6a5b949f7ab9fbe72703/docs/public/assets/screenshot/dark.png) | ![Noctis](https://raw.githubusercontent.com/aFFekopp/noctis/5515804e4b9fce97eecad21a13e6e7f28a5f2e9b/docs/screenshots/pc/1.jpg) | ![Dartec Graphite, dark](dartec-variants/screenshots/dartec-graphite-room-tablet-dark-en.webp) |
| ![Graphite, light](https://raw.githubusercontent.com/TilmanGriesel/graphite/1e8ab5a5d64a724a8d5c6a5b949f7ab9fbe72703/docs/public/assets/screenshot/light.png) | | ![Dartec Graphite, light](dartec-variants/screenshots/dartec-graphite-room-tablet-light-en.webp) |

### Catppuccin and LCARS: no Dartec variant

- **Catppuccin** is already the best-behaved third-party theme: MIT, theme
  variables only, and screenshots made of built-in cards. Its method (one
  palette in several flavours) is what Dartec Material does with the brand
  teal. A separate pastel Dartec variant would not be calm or on-brand.
- **LCARS** is a costume, not a style for a family home. Most of it is 92 KB of
  injected CSS. It cannot mirror for Arabic, its fonts have no Arabic, and its
  README limits it to personal, non-commercial use.

![Catppuccin's own preview, built-in cards](https://raw.githubusercontent.com/catppuccin/home-assistant/b37ada06d011fe02d487719b06e70e98ded76149/assets/preview.webp)

## What each look would take on a customer home

| Look | Reproduce faithfully | Risk to a fleet | Could Dartec's generator produce it with pinned, low-risk dependencies? |
|---|---|---|---|
| Glass | Theme `v1.3`, card-mod or UIX `v8.3.1`, clock-weather-card `v2.9.5`, week-planner-card `v1.14.1`, navbar-card `v1.6.1` (no licence), and a photograph | High: JavaScript in the homeowner's browser, a runtime CDN image, blur cost on tablets, contrast dependent on the photo | **Yes, as Dartec Glass**: theme variables only, and a gradient instead of a photo |
| Soft | Theme `v3.0.3` (currently broken), plus mini-graph `v0.13.0`, mini-media-player `v1.16.12`, button-card `v7.0.1`, plus Apple wallpapers | Medium to high, and the wallpapers cannot be shipped at all | **Yes, as Dartec Soft**: theme variables only |
| Material | Theme `5.0.15`, plus material-you-utilities `2.1.26` (JavaScript) for the full look | Low for the theme, high for the utilities | **Yes, as Dartec Material**: theme only |
| Graphite / dark | Theme `2.7.6` | Low | **Yes, as Dartec Graphite**: theme only |
| LCARS | Theme, UIX, lcars.js, self-hosted fonts | High, and a licence restriction | No |
| Catppuccin | Theme `v2.1.3` | Low | Already possible. Listed in [themes.md](themes.md) as a customer choice |

Nothing on this page requires card-mod, UIX, custom cards or images. The six
Dartec drafts are one theme file of variables.
