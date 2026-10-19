---
name: unhoard — Card page
description: Mission-control console for triaging X bookmarks into Go, Later or Skip
colors:
  room: "#dad8cd"
  panel: "#f3f1e8"
  panel-hi: "#e6e3d6"
  well: "#fbfaf4"
  track: "#d9d6c8"
  seam: "#c9c5b4"
  seam-hi: "#8c8775"
  ink: "#1c1e1a"
  ink-2: "#43463e"
  ink-3: "#555a4c"
  go: "#3cc169"
  go-well: "#cfe5d1"
  go-text: "#0f5f2c"
  go-line: "#3d8f58"
  go-ink: "#04200e"
  go-dot: "#1c9447"
  hold: "#f4b32a"
  hold-well: "#f3e3b0"
  hold-text: "#6e4600"
  hold-line: "#a8741a"
  hold-ink: "#2b1b00"
  hold-dot: "#b86f00"
  scrub: "#e9604f"
  scrub-well: "#f1d2cb"
  scrub-text: "#9a2216"
  scrub-line: "#b5584b"
  scrub-ink: "#2a0803"
  scrub-dot: "#cf3f2e"
  tape: "#f6efc9"
  tape-line: "#b9ab70"
  tape-ink: "#1c1e1a"
  x-blue: "#1d9bf0"
  x-blue-text: "#0b6fb5"
  x-bg: "#ffffff"
  x-border: "#cfd9de"
  x-rule: "#eff3f4"
  x-ink: "#0f1419"
  x-ink-2: "#536471"
typography:
  mark:
    fontFamily: "system-ui, -apple-system, \"Segoe UI\", Roboto, sans-serif"
    fontSize: "20px"
    fontWeight: 700
    letterSpacing: "0.01em"
  task:
    fontFamily: "system-ui, -apple-system, \"Segoe UI\", Roboto, sans-serif"
    fontSize: "17px"
    fontWeight: 650
    lineHeight: 1.3
  body:
    fontFamily: "system-ui, -apple-system, \"Segoe UI\", Roboto, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  small:
    fontFamily: "system-ui, -apple-system, \"Segoe UI\", Roboto, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
  legend:
    fontFamily: "system-ui, -apple-system, \"Segoe UI\", Roboto, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    letterSpacing: "0.08em"
  x-post:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif"
    fontSize: "15px"
    lineHeight: "20px"
  x-meta:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif"
    fontSize: "13px"
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12px"
rounded:
  lamp: "6px"
  panel: "12px"
  tape: "2px"
  field: "8px"
  x-card: "16px"
components:
  lamp-go:
    backgroundColor: "{colors.go-well}"
    textColor: "{colors.go-text}"
    rounded: "{rounded.lamp}"
    padding: "11px 8px"
  lamp-go-lit:
    backgroundColor: "{colors.go}"
    textColor: "{colors.go-ink}"
    rounded: "{rounded.lamp}"
    padding: "11px 8px"
  lamp-later:
    backgroundColor: "{colors.hold-well}"
    textColor: "{colors.hold-text}"
    rounded: "{rounded.lamp}"
    padding: "11px 8px"
  lamp-later-lit:
    backgroundColor: "{colors.hold}"
    textColor: "{colors.hold-ink}"
    rounded: "{rounded.lamp}"
    padding: "11px 8px"
  lamp-skip:
    backgroundColor: "{colors.scrub-well}"
    textColor: "{colors.scrub-text}"
    rounded: "{rounded.lamp}"
    padding: "11px 8px"
  lamp-skip-lit:
    backgroundColor: "{colors.scrub}"
    textColor: "{colors.scrub-ink}"
    rounded: "{rounded.lamp}"
    padding: "11px 8px"
  tape:
    backgroundColor: "{colors.tape}"
    textColor: "{colors.tape-ink}"
    rounded: "{rounded.tape}"
    padding: "2px 7px"
  readout:
    backgroundColor: "{colors.well}"
    textColor: "{colors.ink-3}"
    rounded: "{rounded.lamp}"
    padding: "5px 10px 5px 8px"
  preflight:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.panel}"
    padding: "8px 12px 9px"
  preflight-next:
    backgroundColor: "{colors.well}"
    textColor: "{colors.ink}"
    rounded: "0"
    padding: "8px 12px 9px"
  x-badge:
    backgroundColor: "{colors.x-blue}"
    textColor: "#ffffff"
    rounded: "50%"
    height: "17px"
    width: "17px"
  x-play:
    backgroundColor: "{colors.x-blue}"
    textColor: "#ffffff"
    rounded: "50%"
    height: "56px"
    width: "56px"
---

# Design System: unhoard — Card page

## Overview

**Creative North Star: "The Launch Room"**

The card page is a 1960s mission-operations console, not a social feed. Today's picks sit as station panels
reporting in; the visitor answers each one by pressing a lamp button — GO, LATER or SKIP — the way a controller
would clear or hold a launch. The room is in its light form (the maintainer's choice, 2026-10-19; it replaced
an earlier dark-room version of the same console): the real Apollo-era control rooms were pale grey-green and
putty enamel, so the panels are pale warm putty on an off-white grey room, with slightly darker bezel strips,
dark engraved legends in tracked caps, label tape and tabular digits in the counters. All of it exists to keep one
gesture, the lamp press, as the only moment of colour the console itself produces.

The system refuses the default grid of identical social cards — but it also refuses to re-skin the post itself.
A saved post renders exactly the way it looks on X: a white "Default" (light) card with its own near-black text,
its own pale borders, its own blue, set inside the putty console rather than painted in console colours.
Saturation elsewhere is rationed: GO (green), LATER (amber) and SKIP (red) are the only hues that mean something
on the console, and they appear on the lamps themselves and on the small status dots that mirror lamp state
(priority dots, readout dots, the annunciator dot, the Launched board's dot). X's own blue is the one hue the
console doesn't ration — it is the embedded post's colour, not the page's, and it never appears outside that
post. There is no confirmed visual anti-reference beyond "not a social card grid."

**Key Characteristics:**
- Pale warm putty-enamel panels (`panel`, `panel-hi`) on an off-white grey room (`room`), with a single
  near-black engraved text family (`ink`)
- Three saturated state hues (`go`, `hold`, `scrub`) rationed to lamps and the status dots that echo them; an
  unlit lamp is a pale lens with a hue-coloured rim and text, a lit lamp fills solid and gets a hard rim
- Dark tracked-caps "legend" labels everywhere a control needs a name
- Tabular-numeral counters and ages, read like a console digit wheel
- Flat, recessed depth (light inset wells with a soft top shadow and a pale bevel) instead of floating,
  drop-shadowed cards
- The post rendered exactly as it looks on X in its light theme, its own white/grey/blue module embedded inside
  the console — the one place X's own blue appears

## Colors

Three equally-weighted saturated hues carry all state; every other console surface is a warm neutral putty or
grey. Each hue comes as a small family: a lit **fill** (the lamp when pressed or hovered), a pale **well** (the
unlit lens), a **text** step dark enough to read on the well and on the panel, a **line** for the unlit rim,
an **ink** for text on the lit fill, and a **dot** step for status dots, picked so each dot reaches 3:1 against
the panel. The one exception to the console palette is the embedded X post, which carries its own literal
palette (below).

### Primary
- **Go Green** (lit fill `#3cc169`, well `#cfe5d1`, text `#0f5f2c`, rim `#3d8f58`, ink on fill `#04200e`, dot
  `#1c9447`): the GO lamp; also the readout "live" dot, the annunciator's default (success) dot, the Launched
  board's "ok" dot and "Printed/Launched" state text, and the arrival-flash keyframe's start colour.

### Secondary
- **Later Amber** (fill `#f4b32a`, well `#f3e3b0`, text `#6e4600`, rim `#a8741a`, ink `#2b1b00`, dot `#b86f00`):
  the LATER lamp; also the readout "held" dot and the text-selection highlight (`::selection`).

### Tertiary
- **Scrub Red** (fill `#e9604f`, well `#f1d2cb`, text `#9a2216`, rim `#b5584b`, ink `#2a0803`, dot `#cf3f2e`):
  the SKIP lamp; doubles as the system's only error colour — the annunciator's error state, high/urgent
  priority dots, failed-launch text. The SKIP lamp uses the same well-to-fill pattern as GO and LATER, just in
  this hue.

### Neutral
- **Room** (`#dad8cd`): the page background — the pale grey "room" the console sits in. Also the backdrop of an
  opened waiting row.
- **Panel** (`#f3f1e8`): console header, station panels, the telemetry list surface (pale putty enamel).
- **Panel Bezel** (`#e6e3d6`): the darker strip under a station head and lamp row, a hovered telemetry row.
- **Well** (`#fbfaf4`): recessed, lit-from-within surfaces — readout chips, the active filter tab, the brief box,
  the note textarea, the links well, the telemetry thumbnail, an avatar image's background while it loads, the
  empty-state code snippet. On a light console a recess reads as a paler window with a soft top shadow and a
  1px seam, not as a darker hole.
- **Track** (`#d9d6c8`): the filter switch's track, the one genuinely darker recess.
- **Seam** (`#c9c5b4`) / **Seam Highlight** (`#8c8775`): the 1px dividers between panels; Seam Highlight also
  colours default (unstated) status dots, the scrollbar thumb and the textarea border.
- **Ink** (`#1c1e1a`): primary engraved near-black text.
- **Ink, Dimmer** (`#43463e`): secondary text — the annunciator message, WHY/brief copy, empty-state text.
- **Ink, Dimmest** (`#555a4c`): tertiary/label text — legends, meta, priority words (at least 4.5:1 on every
  console surface, including Room and Track).
- **Tape** (`#f6efc9`) with its edge (`#b9ab70`) and **Tape Ink** (`#1c1e1a`): the pale label-maker paper chip
  (a bookmark's label, e.g. "work"), edged so it reads as stuck on; Tape Ink is also the dark text on the
  initials-fallback avatar tile.

### The Embedded X Post (its own palette)
The X post does not draw from the console's custom properties at all; it carries a handful of literal, repeated
values of its own, X's light ("Default") theme, kept deliberately separate so the post reads as authentically X
rather than console-themed:
- **X Blue** (`#1d9bf0`): the verified badge, the media Play button, the video `accent-color`, and the tint behind
  the "open on X" icon on hover.
- **X Blue, for text** (`#0b6fb5`): @mentions, #tags, links and the "Show more" button. X's own `#1d9bf0` is only
  3.0:1 on white, which fails AA for 15px text, so text uses this deeper step of the same blue (5.3:1). It is the
  one place the post departs from X's literal colour.
- **X White** (`#ffffff`): the post card's background and the media grid's gutter.
- **X Border** (`#cfd9de`): the post card's border and the media grid's frame; **X Rule** (`#eff3f4`) is the
  placeholder behind media tiles.
- **X Ink** (`#0f1419`): the post's primary text (the author name, the post body).
- **X Ink, Dimmer** (`#536471`): the handle/age line, the "open on X" icon at rest, and the action-row icons and
  counts.

### Named Rules
**The State-Color Rule.** Green, amber and red mean state and appear only on the lamps and the status dots
that mirror them (priority dots, readout dots, the annunciator's dot, the Launched board's dot, the arrival
flash). X's own blue appears only inside the embedded X post.

**The Scrub-Does-Double-Duty Rule.** Scrub Red is both the SKIP lamp's hue and the page's only error colour;
a red dot or red text always means "stopped, failed, or declined," never a third, unrelated meaning.

**The Unlit-Must-Still-Read Rule.** On a light panel a pale lens alone is nearly invisible, so every unlit lamp
keeps a 1px rim in its own hue (at least 3:1 on the panel) and text in its dark text step. Lit means a solid
fill, a darker edge and a hard rim; hover means the solid fill without the rim, so hover never reads as a
decision already made.

## Typography

**Body/Display/Label Font:** `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` for the console chrome
(no web font is loaded anywhere). The one exception is the embedded X post, which swaps to X's own system
stack (see below) to read as authentically X.

**Character:** Across the console, a single workhorse system sans carries every role; hierarchy comes from
size, weight and tracking, not from switching typefaces. `font-variant-numeric: tabular-nums` is set once on
`body`, so every count and age — in the readouts, the priority word, the telemetry rows — lines up like
console digit wheels.

### Hierarchy
- **Mark** (700, 20px): the "Bookmarks" wordmark, console header only.
- **Task** (650, 17px, 1.3 line-height): the task line inside every station — the largest readable text on the
  page after the wordmark, so a visitor who reads only this line still knows what to do.
- **Body** (400, 14px, 1.5 line-height): the page's base text size, set once on `<body>`; most content is
  sized more specifically, so this surfaces directly mainly in the empty-state message.
- **Small** (400, 12.5px): the workhorse secondary size — the WHY and Brief field text, the annunciator
  message, the note textarea, and most of the telemetry row (author name, excerpt, task, fault text).
- **Legend** (600, 11px, 0.08–0.1em tracking, uppercase): engraved tracked-caps labels — field keys, readout
  labels, the priority word, section legends, filter-switch tabs, the lamp labels (set at a close 13px/0.12em
  rather than the Legend token itself).

The embedded X post runs its own local type, in its own font stack (see Colors: the Embedded X Post), not the
console's: **15px/20px** for the author name, the handle/age line and the post text itself, and **13px**
tabular numbers for the reply/repost/like/view/save action row.

One narrow exception on the console side: the `unhoard sync && unhoard enrich` snippet
in the empty state is set in a monospace stack (`ui-monospace, SFMono-Regular, Menlo, monospace`, 12px) to read
as a literal command, not prose.

### Named Rules
**The Task-Is-Loudest Rule.** Within a station, TASK (17px/650) outsizes the post text and the WHY field; it is
the one line the design guarantees is readable without reading anything else on the card.

## Layout

A single 1240px max-width container (`console-in`, the annunciator, and `room`) centers the console on wide
screens. Today's picks render as three equal station panels side by side (`grid-template-columns: repeat(3,
1fr)`, 14px gap), dropping to two columns at ≤1000px and one at ≤680px. The waiting list is a dense 7-column
telemetry grid (status dot, 44px thumbnail, author, excerpt, task, label tape, age) that drops the excerpt and
age columns at ≤1000px and collapses to a 3-column wrapped layout at ≤680px.

Opening a waiting row expands it in place into a full station. Above 1000px its body splits into two columns —
post on the left, everything else (WHY, TASK, brief, note, lamps) on the right — and the right "decision"
column is `position: sticky; top: 96px`, so it rides alongside a long post as it scrolls; the sticky behavior
reverts to normal flow at ≤680px.

There is no named spacing scale; padding and gaps are chosen per component in roughly even 2px steps between
3px and 22px, tightest inside chips and lamps, widest around the page shell (`room` uses 20–22px side/bottom
padding).

## Elevation & Depth

The console is flat with recessed wells, not a stack of floating, drop-shadowed cards. Depth reads as "enamel
panel vs. backlit control": a soft inset shadow with a 1px pale bevel marks recessed surfaces (readouts, the
filter-switch track, the textarea, the brief box, the empty thumbnail), and a 1px Seam border separates panels,
rather than elevation shadows lifting them. The only outward shadows are small and warm-grey — the sticky
header's soft drop and the lamps' tight under-edge — plus a tight ambient shadow under the media Play button.
The embedded X post itself casts no shadow at all; it sits flush with a 1px border, flatter than the console
around it.

### Shadow Vocabulary
All console shadows use one warm shadow tint (`--shade: 60,52,30`), never pure black.
- **Recessed well** (`inset 0 1px 2px rgba(shade,.35–.45)` plus, on readouts and the switch track,
  `0 1px 0 rgba(255,255,255,.8)` as the lower bevel): readouts, switch track, brief box, links well, empty
  thumbnail.
- **Panel lift** (`0 6px 14px -10px rgba(shade,.5)`): the sticky console header only.
- **Lamp edge** (`inset 0 1px 0 rgba(255,255,255,.65), 0 2px 2px -1px rgba(shade,.4)`): an unlit lamp, a pale
  top highlight and a tight under-edge.
- **Lit-lamp edge** (`inset 0 1px 0 rgba(255,255,255,.5), inset 0 -2px 0 rgba(shade,.2)`, plus
  `0 0 0 3px` in the lamp's own hue at 50% when lit): hover shows the first part, a pressed (lit) lamp adds the
  rim.
- **Play-button ambient** (`0 0 4px rgba(0,0,0,.4)`): the media Play button inside an X post, a tight
  symmetric shadow rather than a directional drop.

### Named Rules
**The No-Glow Rule.** Every shadow is a tight drop or an inset recess; nothing uses a blurred, coloured halo,
even around the saturated lamps. A lit lamp is marked by a solid hard-edged rim (zero blur), which is a bezel,
not a glow.

## Shapes

A small, climbing corner scale: 2px (the label tape chip) < 6px (`--r-lamp`: lamps, readouts, the
filter-switch track) < 8px (the brief field and the note textarea) < 12px (`--r-panel`: stations, the
telemetry list) < 16px (the embedded X post card and its media grid — X's own, larger corner radius, distinct
from the console's own scale), plus full circles for every avatar (the X post's and the telemetry row's),
status dots, the verified badge and the media Play button. Console-side borders are a consistent 1px Seam;
the X post uses its own 1px X Border instead.

## Components

### Lamps (Go / Later / Skip)
A row of three buttons in a 2fr/1fr/1fr grid, so GO is visibly the largest target — the system's one deliberate
size difference among the three. At rest each lamp is an unlit pale lens: its own "well" background, a 1px rim in its hue and dark text in its
hue's text step. On hover it fills solid with dark ink text and a thin highlight; once lit it keeps that fill
and adds a hard 3px rim in its own hue. Clicking any lamp disables all three until the request resolves (the
two that were not chosen fade to half strength), and the chosen one gets a `lit` class. Go,
Later and Skip otherwise share one visual pattern (well background → full fill) differing only by hue and, for
Go, footprint.

### Console readouts
Small chips in the header (`Today`, `Waiting`, `Later`, and, once any card has launched, `Launched`), each a
label/value pair with a leading status dot. The dot is coloured only for the roles the data calls "live" (green)
or "held" (amber); an inactive readout's dot stays Seam Highlight grey.

### Setup lamp
A readout-style button in the header, after the counters: a status dot, the legend "Setup" and a ready count ("4/6").
The dot is green when every checklist item is ready and amber otherwise (the same state rule as the other readouts).
It is shown whenever the server answered `/api/setup`, and toggles the pre-flight checklist (`aria-expanded`). On
phone width it sits on the wordmark's row, on the right.

### Pre-flight checklist
A first-run panel in the console's own idiom, not an onboarding modal: one Panel-coloured block above the poll, a
Bezel head strip with the legend "Pre-flight checklist", a "N of 6 ready" count and a small Hide button, then six cells
in a three-column grid (two columns at <=1000px, one at <=680px) divided by 1px Seam rules rather than nested cards.
Each cell has a status dot, a legend label (X access, User id, Agent, Launch, Data, Samples), a status word (Ready,
To do, Check, and Next on the one step to take now), a one-line small explanation, and, when there is something to run,
the exact command in a code-font Well with a Copy button. Ready items show no command. The Next cell is set in the
Well colour with a soft inset, as if lit from within.

The dot follows the state rule: filled green for ready, filled amber for check, and for "to do" an unlit ring in Seam
Highlight (an unlit lamp, never red: nothing has failed yet). The status word repeats the state in text, so colour never
carries it alone.

States:
- **Open by itself** until the server reports a real (non-demo) enriched bookmark; with only the fictional demo cards
  the Samples cell says "You are looking at sample cards. To use your own bookmarks:" followed by the next command.
- **Hidden** automatically once a real enriched bookmark exists, and when the visitor presses Hide (remembered in this
  browser's `localStorage`, guarded by try/catch; the checklist works without storage). The Setup lamp reopens it
  at any time, whether or not it was hidden.
- **Copy**: the command text is selected (so Ctrl+C always works), then written with `navigator.clipboard`; the button
  reads "Copied" (or "Press Ctrl+C" when the browser refuses) for two seconds and a hidden live region says the same.
- **Phone width**: cells run one per row, items that are not Next show their explanation on one line (ellipsis), and
  the command wraps under it.
- **No answer from `/api/setup`**: the panel and the lamp stay hidden; the page is otherwise unchanged.

Commands are fixed strings (plus a configured server address that matched a strict address pattern); everything is set
with `textContent`.

### Annunciator
A single status line below the header (`role="status"`, `aria-live="polite"`), hidden until there is something
to say. A leading dot and the message color flip from Go Green to Scrub Red on error; a failed load appends an
inline "Retry" button that re-runs the failed request.

### Station (pick card, and an opened waiting row)
One console panel reporting on one bookmark: a head row (priority dot, label tape, "saved Nd ago"), then the
embedded X post, the WHY field, the TASK field, a "Links in this task" field when the server found URLs in the
task text, the brief, a note textarea, and the lamp row. The brief is always shown: the user reads what Go
will launch, and the links in it, before pressing it. The expanded (`full`) variant used for an opened waiting
row switches to the two-column layout described in Layout.

The "Links in this task" field (a legend key like WHY/TASK) lists the URLs the server found in the task text
(title, brief, why saved), at most 10, with "+N more not shown" when it left some out. They sit in a recessed
Well in the code font, in Dimmer ink with no colour of its own. Each URL is printed in full as text
(`textContent`), scheme as written. Only an `https://` URL is a link (`rel="noopener noreferrer"`); the rest are
plain text. Invisible and direction-changing characters (such as U+202E) are shown as `<U+XXXX>` markers, and
a URL that has any is plain text.

### X post (the post)
The post rendered exactly as it looks on X, not re-themed to the console: a flat white card (X's light
"Default" theme), a 1px X Border, 16px corners, and its own font stack. A 40px circular avatar sits left of a body column: the author name
(bold), an optional verified badge (a 17px X Blue circle with a white check, shown when the source card says
the author is verified), the handle and age, and an "open on X" icon link that tints X Blue on hover. Post
text keeps @mentions, #tags and links in the deeper text blue with no underline at rest (underline on hover).
A "Show more" button, also in the text blue, appears only when the 9-line clamp actually truncates the text (measured after render)
and removes itself otherwise. Below the text: a 1–4 tile media grid, then, if the post has metrics, an action
row of up to five icon+count pairs (replies, reposts, likes, views, saves) in X Ink Dimmer, each with an
`aria-label` carrying the full count and the metric name.

### Media grid
Part of the X post, themed to match it (pale `X Rule` tile placeholders, X Border frame, white gutter, 16px corners) rather than the
console. Photos and GIFs render immediately with `loading="lazy"` and `referrerpolicy="no-referrer"`; GIFs
autoplay muted and looped unless reduced motion is requested. Video tiles carry no `src` and `preload="none"`
until the visitor presses the X Blue, white-bordered Play button — the click sets `src`, turns on native
controls, and removes the button. The duration/GIF badge sits bottom-left on the tile. Nothing in the media
grid borrows console tokens or console lamp color; the Play button's hover state stays inside X Blue.

### Telemetry row (waiting list)
A dense grid list, keyboard-focusable (`tabindex="0"`, Enter or Space opens it the same as a click), that
hover-tints with the Panel Bezel colour. A read-only variant on the Launched board drops the hover tint and
pointer cursor, uses a Go-green "ok" or Scrub-red "failed" dot, and plays a 1.6s keyframe flash from the Go-well
colour back to transparent on the row a card has just launched into.

## Do's and Don'ts

### Do:
- **Do** keep the pre-flight checklist compact (three columns at desktop width, status in one line of text) so today's
  picks stay in the first viewport, and never let it become a modal or a wizard: Hide and the Setup lamp are the only
  controls besides Copy.
- **Do** keep saturated color (`go`/`hold`/`scrub`) confined to the three lamps and the status dots that mirror
  their state; every other console surface stays warm putty, pale grey or near-black ink.
- **Do** render the post exactly as it looks on X in its light theme — its own white/grey/blue palette and font
  stack — and keep that palette self-contained; don't pull console tokens into the post, and don't let X Blue
  leak outside it.
- **Do** leave every video tile without a `src` and with `preload="none"` until its Play button is pressed;
  never prefetch from a video host.
- **Do** restrict image sources to `https://` URLs or `data:image/` data URIs; never pass through other
  schemes.
- **Do** honor `prefers-reduced-motion`: disable CSS transitions, skip GIF autoplay, use instant (not smooth)
  scrolling, and use a 0ms (not 230ms) delay before refreshing after a decision.
- **Do** keep the annunciator as a live region (`role="status"`, `aria-live="polite"`) and telemetry rows
  keyboard-operable (`tabindex="0"`, Enter/Space); a `:focus-visible` outline (2px near-black, offset 2px) must stay visible everywhere; the note textarea shows
  focus as a doubled near-black border.
- **Do** keep text at WCAG AA (4.5:1) on the surface it sits on, and every unlit lamp's rim and every status dot
  at 3:1 on the panel.
- **Do** render post and agent-authored text with `textContent`, never `innerHTML`.

### Don't:
- **Don't** add a coloured or blurred glow anywhere, including on a lit lamp; depth comes only from inset
  recesses, tight warm-grey drops and the lit lamp's hard-edged rim.
- **Don't** bring the dark room back as a toggle or a `prefers-color-scheme` branch: light is the page's one
  look (maintainer's choice, 2026-10-19).
- **Don't** load a web font or a third-party script; the system runs on system fonts and two local files
  (`app.css`, `app.js`) — the X post's own font stack is still system-native, never a webfont.
- **Don't** treat the label tape (`tape`/`tape-ink`) as a brand accent colour; it is the label chip's paper
  colour, not a highlight.
