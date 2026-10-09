---
name: JCC-CCM 노드 모니터
description: Staff dashboard for panel monitoring, built like a native iOS/macOS app.
colors:
  ground: "#f2f2f7"
  canvas: "#f2f2f7"
  surface: "#ffffff"
  surface-2: "#f2f2f7"
  ink: "#1c1c1e"
  ink-2: "#3c3c43"
  ink-3: "#8a8a8e"
  rule: "#d8d8dc"
  rule-2: "#e9e9ee"
  line-3: "#c6c6c8"
  accent: "#007aff"
  accent-soft: "#e5f0ff"
  wire: "#007aff"
  port-sensor: "#34c759"
  port-agg: "#af52de"
  grid: "#e9e9ee"
  grid-major: "#e5e5ea"
  ok: "#248a3d"
  ok-soft: "#e3f6e8"
  warn: "#c93400"
  warn-soft: "#fff1e0"
  crit: "#d70015"
  crit-soft: "#ffe5e7"
  menubar: "rgba(249,249,251,.78)"
  menu-hover: "rgba(118,118,128,.12)"
  ground-dark: "#000000"
  canvas-dark: "#000000"
  surface-dark: "#1c1c1e"
  surface-2-dark: "#2c2c2e"
  ink-dark: "#f5f5f7"
  ink-2-dark: "#d1d1d6"
  ink-3-dark: "#98989f"
  rule-dark: "#38383a"
  rule-2-dark: "#2c2c2e"
  line-3-dark: "#48484a"
  accent-dark: "#0a84ff"
  accent-soft-dark: "#0a2a4d"
  wire-dark: "#0a84ff"
  ok-dark: "#30d158"
  ok-soft-dark: "#0f2e19"
  warn-dark: "#ff9f0a"
  warn-soft-dark: "#3a2706"
  crit-dark: "#ff453a"
  crit-soft-dark: "#3d1210"
  menubar-dark: "rgba(28,28,30,.72)"
  menu-hover-dark: "rgba(118,118,128,.24)"
typography:
  large-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Display, Pretendard, Apple SD Gothic Neo, system-ui, sans-serif"
    fontSize: "34px"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.022em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  row-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "-0.01em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0"
    fontFeature: "tabular-nums"
  footnote:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0"
  caption:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1.35
    letterSpacing: "0"
  title-3:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Display, Pretendard, Apple SD Gothic Neo, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  title-1:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Display, Pretendard, Apple SD Gothic Neo, system-ui, sans-serif"
    fontSize: "28px"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.022em"
  numeral:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Display, Pretendard, Apple SD Gothic Neo, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.02em"
    fontFeature: "tabular-nums"
rounded:
  r: "10px"
  control: "8px"
  card: "14px"
  dialog: "22px"
  pill: "999px"
spacing:
  hair: "4px"
  xs: "6px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  page: "24px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "#ffffff"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "7px 13px"
    height: "36px"
  button-tinted:
    backgroundColor: "{colors.menu-hover}"
    textColor: "{colors.accent}"
    rounded: "{rounded.control}"
    padding: "7px 13px"
    height: "36px"
  segmented-control:
    backgroundColor: "{colors.menu-hover}"
    rounded: "9px"
    padding: "2px"
  segmented-option-active:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "7px"
  search-field:
    backgroundColor: "{colors.menu-hover}"
    textColor: "{colors.ink}"
    rounded: "{rounded.r}"
    padding: "8px 12px"
  status-pill:
    rounded: "{rounded.pill}"
    padding: "4px 10px"
  grouped-row:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    padding: "13px 16px"
  dialog:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.card}"
---

# Design System: JCC-CCM 노드 모니터

## Overview

**Creative North Star: "A native Apple app for the electrical room"**

The staff dashboard looks and behaves like an iOS/macOS app (2026-10-09 redesign; it replaced the earlier
"living electrical drawing" world — sheet borders, zone coordinates, title block, B612 numerals, 2px corners, BIOS login).
A quiet grouped-gray ground (#F2F2F7) carries white cards; the menubar and menus are translucent material
(`backdrop-filter: saturate(180%) blur(20px)`) with content passing under; one accent, system blue, marks every action
and selection. Status speaks only through the iOS system green, orange and red, always with a word.

The first view of the fleet is a large title "현장 목록" (34px, -0.022em) with the panel-status pills beside it,
a segmented control and a search field, then customer groups as inset grouped lists like the Settings app:
a grey 13px group header, white rows with hairline separators, 14px outer corners, a status pill with a dot on the left.

**Key Characteristics:**
- Grouped-gray ground, white cards, hairline (0.5px) separators; depth from soft shadows only where something floats.
- Translucent menubar and dropdowns; reduced transparency switches them to solid.
- One accent: system blue (#007AFF / #0A84FF dark). Selection, primary buttons, focus rings, wires.
- iOS status colors: green #248A3D(text)/#34C759, orange #C93400(text)/#FF9F0A, red #D70015/#FF453A.
- Corners: 10px default, 8px controls, 14px cards and dialogs, 22px sign-in card, pills 999px.
- Type: system font on Apple devices (SF Pro / Apple SD Gothic Neo), Pretendard elsewhere (self-hosted, OFL).
  Large text tightens (-0.02em), body stays at 0; all numbers use tabular figures of the same face.
- Sign-in is an Apple-style card (로그인 title, two link-status dots, filled fields, full-width blue button, thin progress bar).

## Colors
- **Ground / Canvas** `#F2F2F7` (dark `#000`): app background, graph canvas (with a faint 24px dot grid).
- **Surface** `#FFF` (dark `#1C1C1E`): cards, grouped rows, dialogs, inspector. **Surface 2** `#F2F2F7` (dark `#2C2C2E`): filled fields, value pills.
- **Ink** `#1C1C1E` / `#3C3C43` / `#8A8A8E` (dark `#F5F5F7` / `#D1D1D6` / `#98989F`): label, secondary label, tertiary label.
- **Separator** rule `#D8D8DC` (dark `#38383A`), hairline rule-2 `#E9E9EE`.
- **Fill** menu-hover `rgba(118,118,128,.12)` (dark `.24`): segmented-control track, tinted buttons, search field, menu hover.
- **Accent** system blue — the only decorative-free hue for action and selection; wires on the single-line diagram use it too.
- **Status** green/orange/red with their soft washes for pills; red is danger only.

**The Word-Plus-Color Rule** still holds: every status color comes with a written label (위험, 주의, 정상, 연결 끊김).

## Typography
- Stack: `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", system-ui`.
  `--mono` uses the same stack (Apple uses SF everywhere except code); numbers rely on `tabular-nums`.
- Large title 34/700/-0.022em · dialog title 17/600/-0.01em · row title 15/600 · body 13/400 · footnote and section headers 12–13/600 in ink-3 (no all-caps, no tracking).
- **Size ramp = iOS text styles only:** 11 (caption, the floor — nothing smaller) · 12 · 13 · 15 · 17 · 20 · 22 · 28 · 34px. Odd sizes (7.5–10.5, 11.5, 12.5, 14, 19…) were folded onto this ramp on 2026-10-09; long node labels ellipsize and show in full in the inspector.

## Components
- **Buttons:** primary = filled blue, white text; secondary = tinted (fill + blue text); destructive keeps red text on fill. Press scales to 0.98.
- **Segmented control:** grey track, the selected segment is a white raised chip (`0 1px 3px rgba(0,0,0,.12)`).
- **Grouped list rows:** fleet rows; the first and last rows round to 14px; hover darkens the row by 8% ink.
- **Status pill:** 999px, soft wash, 7px dot + word.
- **Dialogs (cmsn-shell, modal):** 14px corners, 0.5px border, popover shadow, header with a hairline; close is a round grey 30px button.
- **Nodes (single-line diagram):** white 12px-corner cards with a hairline header; warn/crit heads keep the solid state color.
- **Menubar:** 46px translucent bar; view switcher is a segmented control; icons in ink-2.

## Do's and Don'ts
- **Do** put new colors on the tokens above (both themes); never hard-code a hex in component CSS.
- **Do** keep reduced motion and reduced transparency fallbacks.
- **Don't** bring back the drawing-sheet devices (heavy 2px outlines, zone coordinates, title-block cells, mono all-caps labels, square corners).
- **Don't** use emoji as icons; drawn line icons only.
- **Don't** add a second accent hue; status colors are not decoration.

### Panel settings hub (staff, `#ps-overlay`)
One dialog per panel: a 3×3 grid of tiles (온도·습도·화재 징조 / 단자 발열·내부 발열·공조 장치 / 설치 환경·기타 센서·판넬 정보).
Each tile is centered: a large drawn line icon (46px, 1.4 stroke — no emoji) on an 84px round plate, name, one-line current value, and a corner dot. State colors the plate (dashed = not entered, warn / crit ring and icon) and the dot; no sensors = dimmed. Hover tints the plate with --accent.
Opening a tile swaps the body for that item's form only (back arrow returns to the grid); each item saves on its own. Staff tokens only (10px radius, hairline separators, --accent on hover).

## Customer surface — JCC GUARD (`server/static/guard.html`)

A separate visual world for customer accounts and sales demos; the staff (Apple) rules above do not apply there.
The tokens live in `guard.html` `:root` (day) with the dark night values under `prefers-color-scheme: dark` and `[data-theme="dark"]`.

- **Concept:** premium car instrument cluster. One large ring "안심 지수" (0-100) is the hero; everything else is quiet.
- **Question cards:** the home is a set of question cards (지금 우리 공장은? · 제가 할 일이 있나요? · 판넬은 각각 괜찮나요? · 이번 달 JCC가 한 일 · 문제가 생기면 누구에게?), each a one-line answer plus 자세히; explanations live behind 자세히.
- **Verdict hero:** the 지금 우리 공장은? card is the peak of the page: full width, always on the login's night plate `--night` #0e1114 (day or night mode alike), answer in `--t-hero` clamp(38-76px) weight 300 (phone `--t-hero-m` clamp 36-46px), ring figure `--t-hero-fig` clamp(44-80px). In danger the whole plate turns `--night-crit` = Rittal red #e50043 with white type. Other cards' answers drop to `--t-fig` so the hero stays the only loud thing.
- **Game-menu tiles:** each question card starts with a 48px rounded plate holding a drawn line icon (26px, stroke 1.7) tinted by meaning (할 일 = the most urgent item's state color, 이번 달 = safe green, contact and reports = ink); the answer is one big figure in `--t-ring` weight 300 with a small 600 unit (2건, 527시간); the whole card is tappable and lifts 3px on hover (reduced motion: none). Reports are six icon tiles (2 x 3 on phones, 6 across on PC: 월간·점검·사건·끊김·확인서·내려받기); panel cards show 30px figures and a tinted status chip.
- **Weight and depth:** card titles 700 at `--t-h` in ink; tile figures 700 at `--t-tile` clamp(56-80px); the hero answer and ring figure 600. Each tile carries one oversized cropped line drawing of its own icon (172px, 9% of its tone) bottom-right; light mode tiles get a soft offset shadow. The month tile is the green night plate `--night-ok` #11291e; the contact tile's call button is full width.
- **Every sensor on the card:** a panel card lists every sensor its nodes carry (the same set the staff node view shows), under a hairline: line icon per kind + one word or number (door 열림/닫힘 by that sensor's own warn threshold, smoke 감지/없음, others number + unit) + the sensor name. Unknown or raw analog channels and aircon link values are left out; quiet by default, a sensor with an active alarm gets a warn/crit tint. The panel sheet's 지금 tab repeats the full list as rows (달린 센서 N개).
- **Traffic light:** status always shows color and shape together: 정상 ✓ circle green, 주의 △ triangle amber, 위험 octagon red, 끊김 plug grey (drawn line icons, never emoji). Panel cards stay neutral when fine (status = green check + grey word, sensor icons grey); warn/crit get a 1.5px border in the state color and a solid status chip (crit #e50043/white, warn #e9b45a/ink; dark: crit #ff3d6e/near-black). A sensor past its own warn/alarm threshold (same comparison as the staff alarm judge) or with an active alarm colors only its icon and value. No tinted card fills or pastel boxes. To-do rows lead with the icon.
- **Signal loss is not danger:** on the customer screen a sensor/CCM that stops sending (silent alarm) never raises the red incident card or a 위험 state; the panel goes 주의 with one line that follows the recovery order — "자동 복구 중" first, and only after channel and CCM restarts gave up, "자동 복구가 안 돼 JCC 직원이 확인합니다". The staff screen keeps it 위험 so it gets fixed fast.
- **Safety certificate (안전 관리 확인서):** a document, not a dashboard: number + 귀중 line over a 2px ink rule, one sentence of what is certified, four figures 2 x 2, compact 12px tables (panels, 위험 경보 with seconds-to-self-action and minutes-to-ack, inspections), the fixed disclaimer, then issue date / reviewer and the JCC Solution sign-off. Demo copies say 시연 문서 in the number line.
- **Korean / English:** a 한/EN button in the header and on the login screen; first visit follows the phone language, the choice is remembered. The page code stays Korean; a translation layer rewrites rendered text (exact dictionary `EN`, number patterns `EN_RE`, then split by sentence / ' — ' / ' · ' and translate each piece; dates and hours converted). Server phrases go through the same dictionary. Names people gave (panels, customers, people) stay as typed. A new customer-facing phrase needs one `EN` line, otherwise it shows in Korean.
- **Pictures over sentences:** customers do not read paragraphs. New information arrives as icon chips, gauges, big numbers and color first; sentences live behind 자세히. Example: vent open = three chips (wind icon 벤트 열림 tinted green with a gentle drift, thermometer + ℃, drop + %), not a paragraph; the incident card no longer lists its steps as text (the gauge says it).
- **Resolution gauge:** the incident card shows progress like a file-copy bar, counted in stages, never time (감지 → 판넬 자동 조치 only when the panel actually acted → JCC 확인 → 정상 복귀): big % + '다음: …', segmented bar (done = safe green, current = flowing stripes, rest = track), stage names under it. No remaining-time estimate — it would be invented. Motion: the first time an incident appears the finished segments fill left-to-right one after another (0.8s each, 0.28s apart) while the % counts up from 0; when a stage completes later only the new segment fills and the number counts from the old % (timer-driven so a background tab still lands on the right number). Reduced motion: final state at once.
- **Login radar:** the login's night panel carries a large live radar (rings, 72 ticks, crosshair, a 6s green sweep with a bright leading edge, two center pings 3s apart, six panel blips that flash as the sweep passes, one amber). Its center sits on the panel's right edge so the copy on the left stays readable; on phones it shrinks into the header corner at 60%. Reduced motion: still.
- **Radar:** behind the hero ring, two faint rings and a sweep turn once every 7s (safe green, watch amber; danger white at 2.6s; stopped when offline or under reduced motion). It is the one authored motion and says "24시간 지켜보는 중".
- **Color:** day ground `#f1efea`, card `#ffffff`, ink `#1b1a17`; night ground `#0b0d10`, card `#15191d`, ink `#e9e6df`.
  State colors only: safe `#2f9a6c`/`#7fd1a8`, watch `#b9822a`/`#e9b45a`, danger = Rittal red `#e50043` (RAL 35745, the colour rittal.com uses) / night `#ff3d6e`.
  Danger never tints the ground or fills cards with pale pink: it shows only as the red hero plate, 1.5px red borders (panel card, incident card) and solid red chips/plates (위험 pill, to-do plate). It is the only time red appears.
- **Type:** Gothic A1 only. Big figures weight 300 (64px ring, 30px month headline, 21-26px stats), headings 600, labels 11px with 0.14-0.16em tracking.
  Type steps are tokens `--t-cap` 12 / `--t-body` 15 / `--t-h` 19 / `--t-fig` 24 / `--t-big` 30 / `--t-ring` 64px (each step at least 1.25x); reading text (timelines, reports) uses body 15.
- **Shape:** radius tokens `--r-s` 10px (controls, small items), `--r` 14px (cards), `--r-l` 22px (big panels, sheets), `--pill` 999px.
- **Motion:** ring fill 1.1s ease-out, color 0.6s, sheets slide up 0.32s; first paint has no transition (no green flash during an incident). Reduced motion turns all off.
- **Ease of use:** phones get a fixed bottom bar (현황 · 판넬 · 지켜낸 것 · 보고서); on PC the reports sit in the 지켜낸 것 card, so the header holds only the mark, site name, night/day and logout; every control is at least 40px;
  the state line shows the last update time; panel metrics carry a one-line plain explanation; staff wording (e.g. 접점 발열 잔차) is rewritten for customers;
  Korean breaks between words (`word-break: keep-all`); no small label above a heading (the month header is a working month switcher).
- **Forecast chart** (panel sheet): actual = solid ink, forecast = dashed safe-green with a light band (past error), last year = dotted ink-3, warn threshold = dashed warn; the SVG is drawn at the real container width so labels stay 12px on phones; the hourly table sits under it and the method + measured average error is always stated.
- **One thing at a time:** the month card shows one sentence, three key figures and two links (이번 달 자세히 · 보고서); the ledger and the four report kinds live behind them. The panel sheet opens on tabs (지금 · 내일 · 원인·할 일 · 공조 · 기록) with sticky pill tabs; only one pane shows; a level≠ok cause shows as a single flag line on 지금; the 24-hour forecast table sits behind 시간별 보기; tabs with no data are dropped.
- **Quiet by default:** panel cards have no border until something needs attention (then the border and status word take the state color); lists inside a card are hairline rows, never boxes inside the box; empty stats are hidden rather than shown as "—".
- **Don't** show a number that is not from real records; assumptions (the patrol comparison) are labeled "추정" with their basis.
- **Don't** give customers output controls (vent, heater, fan). Viewing, acknowledging, and a phone call only.
