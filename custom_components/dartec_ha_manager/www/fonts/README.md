# Fonts the agent serves

Served at `/dartec_branding/fonts/` and loaded by `../dashboard-fix.js`, so
the Dartec themes' `ha-font-family-*` variables resolve on every page
(dartec-ha-manager#59, recommendation R15).

| File | Face | Weight in CSS | Source | Licence |
|---|---|---|---|---|
| `Lateef-Regular.woff2` | Lateef Regular 4.400 | 400 | SIL Global | `OFL-Lateef.txt` |
| `Lateef-Medium.woff2` | Lateef Medium 4.400 | 500 | SIL Global | `OFL-Lateef.txt` |
| `Lateef-Bold.woff2` | Lateef Bold 4.400 | 600 700 | SIL Global | `OFL-Lateef.txt` |
| `IBMPlexMono-Medium-Latin1.woff2` | IBM Plex Mono Medium 2.005 | 500 | IBM, `@ibm/plex-mono` 2.5.0 | `OFL-IBMPlexMono.txt` |

All four are **unmodified**, because both licences reserve the font's name
("Lateef" and "SIL"; "Plex") and the SIL Open Font License does not let a
modified version keep a reserved name (OFL condition 3). A subset is a
modified version (OFL FAQ 2.6). So:

- **Lateef** is SIL's own TTF, byte for byte the file in `google/fonts`
  `ofl/lateef/` (taken from SIL's `silnrsi/font-lateef` at d682ea7),
  compressed to WOFF2 with `fontTools.ttLib.woff2.compress` and nothing else.
  Decompressed again, every table is identical to the TTF except `head`, where
  WOFF2 itself sets flag bit 11 ("lossless transformation") and so the
  checksum. That is the case OFL FAQ 2.2.1 allows under the original name. No
  WOFF metadata block is added.
- **IBM Plex Mono** is IBM's own Latin-1 web file, copied from the
  `@ibm/plex-mono` 2.5.0 npm package (published by IBM's Carbon team; the
  file matches the package tarball's sha512 integrity), with that package's
  `LICENSE.txt` as `OFL-IBMPlexMono.txt`.

The storefront's own web files (`smarthome-planner/assets/fonts/`,
`dartec-lateef-*-ar.woff2` and `dartec-plex-mono.woff2`) are subsets that keep
the reserved names, so they are **not** used here.

To replace one: take the upstream file, check it the same way, give it a
**new file name** (the static path is cached for 31 days, so a changed file
under an old name would not reach browsers), and update `dashboard-fix.js`.

sha256:

```
dffdb2660c5508eace5dc7492c9198faee3ce149d88bfac2bc9d9d87521a70ad  Lateef-Regular.woff2
e4e8baaa35502fe4f91b052505d4b74d18075041c73dafbbbce53d766470c981  Lateef-Medium.woff2
21c92e1138dc4470e690f97e405f7243e1c7797541f60c42d1495445254ae863  Lateef-Bold.woff2
41201b658a328b9d00368215c2f1102770f80b15952ab82631e4006255e6365d  IBMPlexMono-Medium-Latin1.woff2
```

## Why Dubai is not here

Dubai is the brand's Latin face, and this repository is public. Its End User
Licence Agreement (The Executive Council of Dubai, "TEC") does not permit
publishing it:

- 2.2: the licence is "non-transferable, non-sublicensable" and "for the
  Licensee's own personal or internal business purposes only".
- 3.1(d): the Licensee undertakes "not to … modify, commercially exploit or
  further distribute (whether commercially or otherwise), the whole or any
  part of the Font Software".
- 3.1(i): "not to provide, or otherwise make available, the Font Software in
  any form, in whole or in part … to any person without the prior written
  consent of TEC".
- 2.2(e): on websites, only "in a secure manner which does not allow an End
  User to access the Font Software outside of the Digital Product".

Dubai could ship here only with TEC's written consent (clause 3.1(i); contact
per clause 8.1 is info@dubaifont.com). Until then Latin text uses the next
face in the theme's stack, and **no Dubai file may be added to this
repository**; `tests/test_fonts.py` fails if one is.
