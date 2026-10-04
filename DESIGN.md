---
name: JCC-CCM 노드 모니터
description: Staff dashboard for panel monitoring, drawn as a living electrical drawing set.
colors:
  ground: "#eceeec"
  canvas: "#f6f7f5"
  surface: "#ffffff"
  surface-2: "#eceeeb"
  ink: "#111416"
  ink-2: "#3e4448"
  ink-3: "#656c71"
  rule: "#c2c7ca"
  rule-2: "#dde0e1"
  line-3: "#111416"
  accent: "#15191c"
  accent-soft: "#e3e6e8"
  wire: "#1d6f87"
  port-sensor: "#2a7048"
  port-agg: "#5d4f86"
  grid: "#e3e6e5"
  grid-major: "#d3d7d7"
  ok: "#22683f"
  ok-soft: "#e1efe6"
  warn: "#8f6200"
  warn-soft: "#f7edd2"
  crit: "#c0231a"
  crit-soft: "#fbe5e2"
  ground-dark: "#0b0d0f"
  canvas-dark: "#0f1215"
  surface-dark: "#14181c"
  surface-2-dark: "#1b2025"
  ink-dark: "#e9ecee"
  ink-2-dark: "#b4bbc0"
  ink-3-dark: "#88919a"
  rule-dark: "#2f363d"
  rule-2-dark: "#21272d"
  line-3-dark: "#e9ecee"
  accent-dark: "#1b7c96"
  accent-soft-dark: "#11303a"
  wire-dark: "#5cc4dc"
  ok-dark: "#6fcf91"
  ok-soft-dark: "#14291c"
  warn-dark: "#e8b53f"
  warn-soft-dark: "#33290f"
  crit-dark: "#ff6a5e"
  crit-soft-dark: "#3a1714"
typography:
  display:
    fontFamily: "Gothic A1, -apple-system, Apple SD Gothic Neo, Malgun Gothic, Segoe UI, sans-serif"
    fontSize: "22px"
    fontWeight: 800
    lineHeight: 1.15
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "Gothic A1, -apple-system, Apple SD Gothic Neo, Malgun Gothic, Segoe UI, sans-serif"
    fontSize: "19px"
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Gothic A1, -apple-system, Apple SD Gothic Neo, Malgun Gothic, Segoe UI, sans-serif"
    fontSize: "14.5px"
    fontWeight: 800
    lineHeight: 1.4
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Gothic A1, -apple-system, Apple SD Gothic Neo, Malgun Gothic, Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0"
    fontFeature: "\"tnum\" 1"
  label:
    fontFamily: "Gothic A1, -apple-system, Apple SD Gothic Neo, Malgun Gothic, Segoe UI, sans-serif"
    fontSize: "10.5px"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "0.02em"
  numeral-display:
    fontFamily: "B612, B612 Mono, ui-monospace, monospace"
    fontSize: "42px"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "-0.02em"
  numeral:
    fontFamily: "B612, B612 Mono, ui-monospace, monospace"
    fontSize: "21px"
    fontWeight: 400
    lineHeight: 1.1
  mono:
    fontFamily: "B612 Mono, SF Mono, Cascadia Mono, Consolas, ui-monospace, monospace"
    fontSize: "11.5px"
    fontWeight: 400
    lineHeight: 1.4
  coordinate:
    fontFamily: "B612 Mono, SF Mono, Cascadia Mono, Consolas, ui-monospace, monospace"
    fontSize: "9.5px"
    fontWeight: 400
    lineHeight: 1.25
rounded:
  r: "2px"
  inner: "1px"
  none: "0"
spacing:
  hair: "4px"
  xs: "6px"
  sm: "8px"
  md: "12px"
  lg: "14px"
  sheet: "22px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "#ffffff"
    typography: "{typography.body}"
    rounded: "{rounded.r}"
    padding: "7px 13px"
    height: "36px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.r}"
    padding: "7px 13px"
    height: "36px"
  button-danger:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.crit}"
    rounded: "{rounded.r}"
    padding: "8px"
  button-danger-hover:
    backgroundColor: "{colors.crit}"
    textColor: "#ffffff"
  segmented-option:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-2}"
    padding: "6px 12px"
    height: "32px"
  segmented-option-active:
    backgroundColor: "{colors.accent}"
    textColor: "#ffffff"
  input-search:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.r}"
    padding: "7px 10px"
    width: "200px"
  status-tag-crit:
    backgroundColor: "{colors.crit-soft}"
    textColor: "{colors.crit}"
    rounded: "{rounded.r}"
    padding: "2px 8px"
  status-tag-warn:
    backgroundColor: "{colors.warn-soft}"
    textColor: "{colors.warn}"
    rounded: "{rounded.r}"
    padding: "2px 8px"
  status-tag-ok:
    textColor: "{colors.ok}"
    rounded: "{rounded.r}"
    padding: "2px 8px"
  status-tag-off:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.r}"
    padding: "2px 8px"
  fleet-row:
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "12px 6px"
  fleet-row-hover:
    backgroundColor: "{colors.canvas}"
  title-block-cell:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    padding: "21px 12px 9px"
  value-pill:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.r}"
    padding: "1px 6px"
---

# Design System: JCC-CCM 노드 모니터

## Overview

**Creative North Star: "The Living Electrical Drawing"**

The staff dashboard is one sheet of a live electrical drawing set. Every major screen sits inside a heavy sheet border with zone coordinates along the edges (1–8 across the top, A–D down the left), carries a title block (drawing name, panel status, author), and lists panels the way a drawing register lists sheets. State is spoken the way a drafter speaks it: with line weight, revision marks, and a written word, never with glow. The graph view continues the same world as a single-line diagram: inked device boxes on a drafting grid, teal wires on the CAD data layer.

Light mode is a plotted sheet: white paper, black ink. Dark mode is CAD model space: ink-black ground, white linework, the data layer in bright teal. Both themes are first-class and toggleable. The density is a working drawing's density: small precise type, tight rows, lots of hairlines, numbers that read like an instrument cluster (Airbus cockpit faces B612 / B612 Mono). The world explicitly rejects the industry default of neon-dark SCADA and frosted glass cards; the build's own overrides strip the earlier glass menubar and gradient AI button down to flat ink.

The bar this system answers to (from PRODUCT.md) is premium instrument clusters, Siemens/Schneider control software, and measuring instruments: restraint, legibility under bright plant light and in dark electrical rooms, and status that never depends on color alone.

**Key Characteristics:**
- A sheet, not a card grid: double-ruled border, zone coordinates, title block, register rows.
- Three line weights carry all hierarchy: hairline, standard rule, heavy 2px outline.
- Square-cut geometry: 2px corners everywhere a corner exists.
- Red means revision and danger only; teal means live data and selection only.
- Numerals in B612 (proportional, compact decimals), codes and coordinates in B612 Mono, Korean in Gothic A1.
- Every status is a word plus a color, and the critical status adds a red revision triangle and a heavy red underline.

**Known exceptions in the build (recorded, not canonized):**
- The boot/login screen is still a green-on-black "secure terminal" demo (its own hard-coded greens and reds, all-caps mono, a ⚠ glyph). It is not in the drawing-sheet world; whether it moves in is an open owner decision. Do not borrow from it for any other surface.
- Fonts load from Google Fonts by `@import` (Gothic A1, B612, B612 Mono) and are not self-hosted yet; offline, the system fallbacks in each stack apply.
- The auto-scan HUD log and node self-heal labels still use emoji/glyphs (⚡, 🔌, ⚠, ♻) and a bright terminal green; graph port and self-heal states use hard-coded system hues outside the token set. These are defects the build carries.

## Colors

A near-monochrome ink-on-paper palette with two meaningful hues: drafting teal for the live data layer and revision red for danger, plus a muted amber and green that exist only as status.

### Primary
- **Drafting Ink** (accent, `#15191c` light / CAD Teal `#1b7c96` dark): the action and selection fill. Primary buttons, the pressed segment of a segmented control, open menu titles, checked checkboxes, focus outlines, text selection. On paper it is ink-black; in model space it becomes teal so a filled control does not vanish into the black ground. White text sits on it in both themes.

### Secondary
- **CAD Layer Teal** (wire, `#1d6f87` light / `#5cc4dc` dark): the live-data layer. Wires in the single-line diagram, data ports, the selected node outline and selected wire (the `sel` token carries the same value). It is never used for decoration or for status.

### Tertiary
- **Port Sensor Green** (`#2a7048` / `#6fcf91` dark) and **Aggregator Violet** (`#5d4f86` / `#a596d8` dark): port-type coding on graph nodes only.

### Status
- **Revision Red** (crit, `#c0231a` / `#ff6a5e` dark) on **Red Wash** (crit-soft): danger. Critical status tags, the revision triangle, the 2px red underline under a critical register row, danger buttons, alarm bell, offline relay pills. This is the JCC red's working role inside the UI; the logo image carries the brand mark itself.
- **Caution Amber** (warn, `#8f6200` / `#e8b53f` dark) on **Amber Wash** (warn-soft): needs attention soon.
- **Running Green** (ok, `#22683f` / `#6fcf91` dark) on **Green Wash** (ok-soft): normal. The ok tag is outline-only (no wash), so a healthy fleet reads quiet.

### Neutral
- **Plot Ground** (ground, `#eceeec` / `#0b0d0f`): the table the sheet lies on; app background behind the fleet sheet.
- **Drafting Canvas** (canvas, `#f6f7f5` / `#0f1215`): the graph canvas and row hover wash.
- **Paper** (surface, `#ffffff` / `#14181c`): the sheet itself, inputs, panels, menus.
- **Paper Shade** (surface-2, `#eceeeb` / `#1b2025`): value pills, neutral chips, off-state fills.
- **Ink / Ink 2 / Ink 3** (`#111416`, `#3e4448`, `#656c71`; dark `#e9ecee`, `#b4bbc0`, `#88919a`): primary text, secondary text, captions and coordinates.
- **Rule** (`#c2c7ca` / `#2f363d`) and **Hairline** (rule-2, `#dde0e1` / `#21272d`): standard rules between rows and cells; hairlines for zone dividers and quiet separators.
- **Heavy Outline** (line-3, `#111416` / `#e9ecee`): the 2px sheet border, title-block frame, menubar baseline, dock top edge, group-heading underline, modal frames.
- **Drafting Grid** (grid `#e3e6e5`, grid-major `#d3d7d7`; dark `#161b20`, `#1e252b`): the graph canvas grid, 24px minor and 120px major.

### Named Rules
**The Two Hues Rule.** Teal is the data layer and selection; red is revision and danger. Neither appears anywhere else. Amber and green appear only as status.

**The Word-Plus-Color Rule.** No status is ever color alone. Every status color is paired with a written label (위험, 주의, 정상, 연결 끊김), and the critical status additionally gets the revision triangle and a heavy red rule.

**The Ink Inversion Rule.** The accent is ink on paper and teal in model space. When designing a filled control, check it in both themes; never hard-code the light-theme black.

## Typography

**Body Font:** Gothic A1 (with -apple-system, Apple SD Gothic Neo, Malgun Gothic, Segoe UI)
**Numeral Font:** B612 (with B612 Mono, ui-monospace)
**Code/Coordinate Font:** B612 Mono (with SF Mono, Cascadia Mono, Consolas)

**Character:** A clean Korean grotesque does all the talking, and the Airbus cockpit faces do all the measuring. Measured values use proportional B612 so the decimal point does not eat a full cell; codes, timestamps, IDs and sheet coordinates use B612 Mono. Tabular figures are on globally.

### Hierarchy
- **Display** (800, 22px, 1.15, -0.03em): the drawing name in the title block (현장 목록). One per sheet.
- **Headline** (800, 19px, 1.2, -0.025em): the inspector spotlight name; modal heads use the same voice at 15px.
- **Title** (800, 14.5px, -0.02em): the panel name on a register row; node titles use 700 11.5px.
- **Body** (400, 13px, 1.5): everything else. Secondary row text 12.5px; captions 11–11.5px in ink-3.
- **Label** (700, 10.5px, 0.02em, sentence case, Gothic A1): section heads (group heading, dock head, alarm panel head, inspector section). Title-block field captions use 600 9.5px ink-3.
- **Numeral Display** (B612 400, 42px, -0.02em): the spotlight reading, unit beside it at 13px mono ink-3.
- **Numeral** (B612 400, 21px): overview cells; 10.5px in value pills.
- **Mono** (B612 Mono 400, 11.5px): connection state, clock (12px), shortcuts (10.5px), wire labels (9.5px, 0.04em).
- **Coordinate** (B612 Mono 400, 9.5px, ink-3): zone numbers and letters on the sheet border.

### Named Rules
**The Korean-Stays-Sans Rule.** Any label that can contain Hangul is set in Gothic A1 at small tracking (0.02em max). Monospace tracking spreads Hangul apart, so the build moved every small heading off B612 Mono; keep it there.

**The Instrument Numeral Rule.** A measured value is set in B612, its unit smaller in mono ink-3, and it is never bolded for emphasis. Emphasis is the status color.

## Layout

The fleet screen is a single centered sheet (max 1160px) on the plot ground, with asymmetric sheet padding (22px top, 26px right/bottom, 40px left) so the left margin can hold the A–D zone column. The top zone strip (1–8) runs inside the border, 8px from the top; the title block is a three-cell grid (1.2fr / 1.4fr / 1fr) with a tool strip spanning beneath it. Register rows are a five-column grid (84px status column, then name, site, reason, last-inspection) with 12px/6px padding and a single standard rule between rows; group headings sit on a heavy 2px rule.

The graph view is a full-bleed drafting canvas (24px minor, 120px major grid) with a right inspector and a bottom dock separated by a heavy top rule. The menubar is 44px with a 2px baseline.

Spacing rhythm is small and drafted: 4 / 6 / 8 / 12 / 14px inside components, 22–26px for sheet margins. Controls meet a 32px (segmented) or 36px (buttons) minimum height.

Responsive: at 1000px the register columns rebalance; at 760px the sheet keeps its border at 10px/8px margins with a tighter double rule, the top zone strip drops to 1–4, the title block becomes two columns with the drawing name spanning, and rows collapse to status + content; at 640px the menubar sheds the edit menu and sound button and tightens to fit 375px phones without horizontal scroll. The clock hides below 1040px and connection text below 900px.

## Elevation & Depth

The sheet is flat. Hierarchy inside the drawing comes from line weight and spacing, never from shadow or gradient. Shadows exist only on layers that genuinely float above the sheet: dropdown menus, modal sheets, and device nodes lifted off the drafting canvas. The sheet's double border is drawn with inset rings (6px paper gap, then a 1px rule inside the 2px outline), which is linework, not elevation.

### Shadow Vocabulary
- **Menu lift** (`box-shadow: 0 12px 28px rgba(8,10,12,.18), 0 2px 6px rgba(8,10,12,.10)`): dropdown menus.
- **Sheet over sheet** (`box-shadow: 0 30px 70px -20px rgba(0,0,0,.5)`): modals, commissioning shell, quick-find, over a `rgba(8,10,12,.58)` scrim.
- **Device on canvas** (`box-shadow: 0 1px 0 rgba(8,10,12,.06), 0 6px 14px -8px rgba(8,10,12,.28)`): graph nodes at rest; selected adds a 1px teal ring.
- **Sheet double rule** (`box-shadow: inset 0 0 0 6px <surface>, inset 0 0 0 7px <rule>`): the fleet sheet border (4px/5px on phones).

### Named Rules
**The Line-Weight Rule.** Three weights only: hairline (rule-2, 1px), standard (rule, 1px), heavy (line-3, 2px). Heavy marks a boundary of a whole thing: the sheet, the title block, the menubar, the dock, a group, a modal head, a critical row.

**The Flat Sheet Rule.** Nothing printed on the sheet casts a shadow. Only menus, modals and canvas nodes float.

## Shapes

Square-cut drafting geometry. Every corner that exists is 2px (`--r`); node heads use 1px inner corners; register rows and the sheet itself are fully square. Circles are reserved for indicator dots (connection, ports, alarm dots). The one silhouette with meaning is the revision triangle (9×8px, clip-path) that precedes a critical status. Step numbers sit in 18px square outlined boxes. Checkboxes are 14px square outlined boxes that fill with accent and an inset paper gap when checked.

## Components

### Buttons
Flat, square, ink-filled; they look like stamped boxes on a drawing.
- **Shape:** 2px corners.
- **Primary:** accent fill, white text, 600–700 12.5px Gothic A1, 7px 13px padding, 36px min height.
- **Secondary:** paper fill, ink text, heavy-outline border in dialogs (standard rule elsewhere); hover darkens the border to ink-3.
- **Danger / Warn (inspector):** paper fill with crit or warn text and border; hover fills solid with white text.
- **Focus:** 2px accent outline, 1px offset.

### Chips and Status Tags
- **Status tag:** 700 11px sans, 2px 8px, 1px border in currentColor, 2px corners. Crit = red on red wash with the revision triangle; warn = amber on amber wash; ok = green outline only; off = ink-2 outline only.
- **Fleet chip:** B612 11px, 1px rule border, transparent; crit variant takes red border and wash.
- **Value pill (graph):** B612 10.5px on paper shade with a rule border; warn/crit switch to their wash, border and text color; stale values drop to ink-3.

### Cards / Containers
There are no floating cards on the sheet. Containers are ruled regions: title-block cells separated by standard rules inside a heavy frame; the inspector spotlight is a 1px-ruled 2px-corner box on paper with 15px padding, its border shifting to accent on hover.

### Inputs / Fields
- **Style:** paper fill, 1px rule border, 2px corners, 500 13px Gothic A1, 7px 10px padding.
- **Focus:** 2px accent outline at 1px offset. (The inspector settings field currently drops the outline and only recolors its border; that is a gap in the build, not a variant.)

### Navigation
- **Menubar:** 44px, paper fill, 2px heavy baseline, no blur. Menu titles 600 13px; the open title fills with accent. Dropdowns are paper with a heavy 1px frame and the menu-lift shadow; items 500 13px, hover fills accent with white text; shortcuts in B612 Mono 10.5px at 60% opacity.
- **View switch (현장 목록 / 배선 보기):** a segmented control with ink dividers; the pressed segment fills accent.
- **Icon buttons:** 17px line icons, 24-unit viewBox, 1.8 stroke, round caps and joins, `currentColor` (sun, moon, expand, shrink, sound, mute, bell, fit). The alarm bell turns red when alarms are active.

### Signature: The Fleet Sheet
The first viewport. A heavy-bordered sheet with zone coordinates (1–8 top, A–D left, B612 Mono 9.5px ink-3 between hairline dividers), a title block whose cells carry small drafting field captions (도면명, 판넬 현황, 작성) above their values, a tool strip (filter segments + search), then register rows grouped under heavy-ruled group heads. A critical row is underlined with a 2px red rule and led by the red revision-triangle tag. Rows hover to the canvas wash; they are the entry into the panel's single-line diagram.

### Signature: The Single-Line Diagram
Device nodes are inked boxes: dark head band (node-head, `#15191c` / `#232a31`) with 700 11.5px white title and a 7.5px mono kind code, a paper body with port rows, value pills on the right. Wires are 1.6px teal paths with 9.5px mono labels; live wires animate a 5/6 dash flow. The canvas is the drafting grid.

## Do's and Don'ts

### Do:
- **Do** put every new staff screen inside the sheet grammar: heavy outer border, title block, ruled regions.
- **Do** use only the three line weights (1px rule-2, 1px rule, 2px line-3) to build hierarchy.
- **Do** keep corners at 2px (`--r`) and reference the token, not a literal.
- **Do** pair every status color with its written word; give critical the revision triangle and the 2px red rule.
- **Do** set measured values in B612 with a smaller mono unit in ink-3, and Hangul-bearing labels in Gothic A1.
- **Do** use the line-icon set (24 viewBox, 1.8 stroke, round, currentColor) for every icon.
- **Do** check every filled control in both themes; the accent changes from ink to teal.
- **Do** give every looping or entrance animation a `prefers-reduced-motion` fallback, as the build does.

### Don't:
- **Don't** use frosted glass, backdrop blur, gradients or ambient glow; the build removed them from the menubar, dropdowns and AI button. The one sanctioned halo is the selected wire's 3px teal drop-shadow.
- **Don't** build the neon-dark SCADA look (glowing green-on-black dashboards); dark mode is CAD model space, not a control-room terminal.
- **Don't** use teal or red for decoration, branding accents, or emphasis; they are data and danger.
- **Don't** put drop shadows on anything printed on the sheet (rows, title block, spotlight, chips).
- **Don't** set Hangul in monospace or track it wider than 0.02em.
- **Don't** use emoji or Unicode glyphs as icons or status markers.
- **Don't** hard-code color hexes in component CSS; use the theme tokens so both themes stay correct.
- **Don't** remove a focus outline without replacing it with the 2px accent outline.

## Customer surface — JCC GUARD (`server/static/guard.html`)

A separate visual world for customer accounts and sales demos; the drawing-sheet rules above do not apply there.
The tokens live in `guard.html` `:root` (day) with the dark night values under `prefers-color-scheme: dark` and `[data-theme="dark"]`.

- **Concept:** premium car instrument cluster. One large ring "안심 지수" (0-100) is the hero; everything else is quiet.
- **Color:** day ground `#f1efea`, card `#ffffff`, ink `#1b1a17`; night ground `#0b0d10`, card `#15191d`, ink `#e9e6df`.
  State colors only: safe `#2f9a6c`/`#7fd1a8`, watch `#b9822a`/`#e9b45a`, danger `#c0362f`/`#ff6b60`.
  Danger tints the whole ground (alert glow) and is the only time red appears.
- **Type:** Gothic A1 only. Big figures weight 300 (64px ring, 30px month headline, 21-26px stats), headings 600, labels 11px with 0.14-0.16em tracking.
  Type steps are tokens `--t-cap` 12 / `--t-body` 15 / `--t-h` 19 / `--t-fig` 24 / `--t-big` 30 / `--t-ring` 64px (each step at least 1.25x); reading text (timelines, reports) uses body 15.
- **Shape:** radius tokens `--r-s` 10px (controls, small items), `--r` 14px (cards), `--r-l` 22px (big panels, sheets), `--pill` 999px.
- **Motion:** ring fill 1.1s ease-out, color 0.6s, sheets slide up 0.32s; first paint has no transition (no green flash during an incident). Reduced motion turns all off.
- **Ease of use:** phones get a fixed bottom bar (현황 · 판넬 · 지켜낸 것 · 보고서); on PC the reports sit in the 지켜낸 것 card, so the header holds only the mark, site name, night/day and logout; every control is at least 40px;
  the state line shows the last update time; panel metrics carry a one-line plain explanation; staff wording (e.g. 접점 발열 잔차) is rewritten for customers;
  Korean breaks between words (`word-break: keep-all`); no small label above a heading (the month header is a working month switcher).
- **Forecast chart** (panel sheet): actual = solid ink, forecast = dashed safe-green with a light band (past error), last year = dotted ink-3, warn threshold = dashed warn; the SVG is drawn at the real container width so labels stay 12px on phones; the hourly table sits under it and the method + measured average error is always stated.
- **Quiet by default:** panel cards have no border until something needs attention (then the border and status word take the state color); lists inside a card are hairline rows, never boxes inside the box; empty stats are hidden rather than shown as "—".
- **Don't** show a number that is not from real records; assumptions (the patrol comparison) are labeled "추정" with their basis.
- **Don't** give customers output controls (vent, heater, fan). Viewing, acknowledging, and a phone call only.
