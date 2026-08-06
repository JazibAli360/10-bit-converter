# Product story and case-study guidance

## Positioning

10-bit Converter is a local Apple-Silicon macOS tool that reduces visible
gradient banding in 8-bit footage and creates a more resilient 10-bit delivery
or grading master.

## The story

The problem usually arrives after the exciting part. A creator has the shot:
an impossible landscape, a fashion film, a moody interior, a generated camera
move. Then the clip goes into a grade, client review, large display, or another
delivery encode. A smooth sky becomes rings. Fog starts to look layered. A soft
wall turns into a staircase.

The composition may still be right. The tonal transition is not holding up.
10-bit Converter is the calm next step: inspect the source, use conservative
debanding and dither, create a verified 10-bit master, and see exactly where
the file will go. It is deliberately a transparent finishing tool rather than
a black-box “enhancer.”

## Audience

- Creators working with AI-generated clips.
- Editors inheriting compressed material.
- Colourists who need a safer intermediate before additional work.

It is most useful for skies, fog, smoke, skin, walls, shadows, and broad
low-texture gradients.

## Honest technical explanation

Colour banding (also called false contouring) turns a smooth tonal transition
into visible rings, stripes, or patches. An 8-bit channel stores 256 possible
values; a 10-bit channel stores 1,024. That does not mean a later 10-bit encode
can retrieve colours missing from an 8-bit source. Its value is reducing the
visibility of existing steps and avoiding needless additional rounding during
post-production.

The default workflow is deterministic and local:

1. Inspect a source frame and scopes.
2. Apply conservative debanding to smooth banded areas.
3. Add controlled dither to make residual steps less visible.
4. Encode a 10-bit HEVC file or ProRes 4444 master.
5. Verify output pixel format and retain the source/output/report record.

## Why this is not AI restoration

Learned restoration tools make interpretive decisions about missing texture or
temporal detail. This product has a narrower contract: reduce a known artifact,
preserve the creator’s existing image, keep processing local, and make no claim
to create new detail or colours.

## Product film

The product film is published with each GitHub Release rather than committed to
the repository. Use the current release asset for a product page, portfolio,
or fork demo. It illustrates the sky-band problem, editing context, and the
application workflow; it is not benchmark evidence or a customer result.

## Research and attribution

Use the canonical links in [SOURCES.md](SOURCES.md). Do not copy external PDFs,
saved webpages, or third-party imagery into a fork without confirming your
rights to redistribute them.
