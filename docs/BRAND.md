# 10-bit Converter brand guide

## Quick reference

- **Product:** 10-bit Converter
- **By:** Jazib Ali 360
- **Positioning:** a calm finishing tool for AI footage whose gradients need to
  survive post-production.
- **Voice:** candid, technically literate, restrained, useful.
- **Core promise:** smoother-looking gradients and a more resilient 10-bit
  master; never invented detail or recovered colour data.

## Messaging

### One-line description

10-bit Converter is a local macOS tool that reduces visible gradient banding
in 8-bit footage and creates a more resilient 10-bit delivery or grading
master.

### The problem in human words

An AI-generated clip can look great until a smooth sky, wall, fog layer, skin
tone, or shadow begins showing rings and steps. The composition is fine; the
tonal transition is not holding together.

### The response

Inspect the source, apply conservative debanding and controlled dither, export
a verified 10-bit file, and show the creator what happened. The tool is a
transparent finishing step, not a black-box “enhancer.”

### Claims we can make

- Reduces visible banding / false contouring.
- Produces a true 10-bit file.
- Creates a stronger intermediate for later grading.
- Processes footage locally and privately.
- Offers HEVC Main10 and ProRes 4444 outputs.

### Claims to avoid

- “Recovers 10-bit information.”
- “Adds real colour data.”
- “Restores missing detail.”
- “Fixes every bad AI video.”
- “More bandwidth” when the intended meaning is tonal precision or grading
  headroom.

## Voice and tone

| Trait | We are | We are not |
| --- | --- | --- |
| Candid | Clear about what the tool cannot do. | Hypey or magical. |
| Literate | Specific about bit depth, banding, and grading. | Needlessly academic. |
| Calm | A practical next step after generation. | A loud generative-toy brand. |
| Friendly | Direct and occasionally dry/funny in social copy. | Flippant about someone’s work. |

### Example wording

**Good:** “A smoother 10-bit master for the grade you still need to do.”

**Good, casual release copy:** “It does not resurrect lost pixels. It just
stops the sky from turning into stairs.”

**Avoid:** “Turn any 8-bit clip into true cinematic 10-bit colour.”

## Visual identity

The interface should feel like a finishing suite: quiet, editorial, precise,
and trustworthy. The navy-to-violet header and warm gradient references are
about the tonal transitions the product protects, not generic “AI glow.”

### UI palette

| Role | Light | Dark | Use |
| --- | --- | --- | --- |
| Canvas | `#f6f5fb` | `#14111f` | App background |
| Surface | `#ffffff` | `#201b31` | Cards and panels |
| Ink | `#171a2c` | `#f0edfb` | Primary text |
| Muted | `#6c7086` | `#aaa3bf` | Supporting text |
| Accent | `#5d3bd1` | `#a78bfa` | Primary actions and selection |
| Header gradient | `#071535 → #142b73 → #3e1b7a` | Same | Product signature |
| Success | `#1a7f37` | `#65d49a` | Verified/safe states |
| Warning | `#8a6d3b` | — | Careful review states |
| Danger | `#d9534f` | — | Destructive/failure states |

Use semantic tokens from `toolkit/index.html`; do not hard-code a new colour
into a component when an existing token applies.

### Typography and UI rhythm

- Use the system sans-serif stack for interface copy.
- Use tabular numerals for media facts, timecodes, percentages, and sizes.
- Keep headings compact; the queue and output evidence are the visual focus.
- Prefer a clear label over an icon-only control. Every icon needs a label or
  accessible name and a visible focus state.

### Icons and imagery

- Use the existing rounded, purposeful SVG icon language; do not introduce
  random emoji or generic AI/starburst icons into the app UI.
- Product imagery should show gradients, workflow, scopes, or the app itself.
- The approved UI visual reference is `design/approved_mockup_v1.html`.
- The product film is distributed as a GitHub Release asset, not committed to
  source control.

## Asset and attribution rules

- Project-owned product assets are documented in
  `case-study-10-bit-converter/SOURCES.md`.
- Keep visible credit and a source link for the supplied UniFab explanatory
  visual. Replace it with an original visual before using the case study in a
  context that requires fully owned media.
- Do not re-upload the supplied research PDF or saved third-party webpage
  snapshots in a fork; use the canonical links in `SOURCES.md` instead.
- Store videos and release builds in GitHub Releases or another media host,
  not Git history.

## Before publishing

- Is every technical claim true of the current build?
- Does the copy say “reduces visible banding” rather than “restores colours”?
- Is the product name and attribution correct?
- Are product film, external imagery, and research sources credited?
- Are colours, tone, and UI language consistent with this guide?
