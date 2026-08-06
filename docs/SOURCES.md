# Research and attribution ledger

The product documentation uses original writing and paraphrases the sources
below. Keep source links near factual claims; do not reproduce long passages.

1. Zhou, R., Athar, S., Wang, Z., & Wang, Z. (2021).
   [Deep Image Debanding](https://arxiv.org/pdf/2110.08569).
   Supports: banding/false contouring as a quantization artifact, its
   visibility in smooth regions, and the trade-offs of debanding/dither.
2. Dare Dreamer Entertainment.
   [Understanding 8bit vs 10bit](https://daredreamer.com/understanding-8bit-vs-10bit/).
   Supports: 8-bit/10-bit channel value counts and post-production latitude.
3. Boris FX.
   [Optics DeBand](https://borisfx.com/documentation/optics-2026/Optics%202026.5/Filters-DeBand.html).
   Supports: the framing of smoothing banded areas while retaining detail.
4. Chan, K. C. K., Zhou, S., Xu, X., & Loy, C. C.
   [BasicVSR++](https://ckkelvinchan.github.io/projects/BasicVSR%2B%2B/).
   Supports: the distinction between deterministic debanding and learned video
   restoration.
5. UniFab.
   [Color Banding in Video](https://unifab.ai/resource/what-is-color-banding).
   Supports: diagnosis through source, grade, export, platform, player, and
   display, plus the limit that a 10-bit workflow does not recreate missing
   source tones.
6. [FFmpeg deband filter documentation](https://ffmpeg.org/ffmpeg-filters.html#deband).
   Implementation reference for the standard pipeline.
7. [PyWebView documentation](https://pywebview.flowrl.com/) and
   [py2app documentation](https://py2app.readthedocs.io/en/latest/).
   Native macOS shell and packaging context.

## Project-owned assets

- App assets under `toolkit/JZB*` and `toolkit/10bit_converter.icns` are the
  repository’s shipped app assets.
- Product film is project-owned media, published as a GitHub Release asset.
- `docs/gradient-banded.png` and `docs/gradient-treated.png` are project-owned
  illustrative comparison graphics; they are not a measured benchmark.
- `docs/8bit-vs-10bit-unifab.avif` is a supplied UniFab reference graphic.
  It is used with a visible link credit on the product page and in the README.
- The repository otherwise omits supplied third-party research files, article
  snapshots, external source icons, and unverified promotional media.
