# QA gates

The automated suite covers the controller and engine contracts. The corpus
manifest in `libplacebo-corpus/manifest.json` defines the remaining hardware
gate for the optional GPU backend.

On the target M3 Mac:

1. Launch the packaged app with no Homebrew, Python, or network available.
2. Confirm Advanced reports the libplacebo engine as available.
3. Run every synthetic fixture through Faithful and libplacebo at matching
   output settings. Record median and 1%-low FPS, memory pressure, and any
   thermal throttling.
4. Verify output format, dimensions, duration, frame count, colour tags,
   audio, metadata, chapters, and supported subtitles.
5. Reject the experimental engine if Vulkan initialization fails, output
   shimmers or changes colour tags, or it regresses throughput without a
   clearly visible quality improvement.

If any gate fails, keep the selector disabled. Faithful 10-bit remains the
shipping default and does not depend on this corpus or a GPU runtime.
# Browser interaction regression

`browser_regression.spec.mjs` exercises the actual queue, per-video profile,
preflight, completion, scopes, comparison, and narrow-window interactions with
route fixtures. It never imports customer videos or writes final exports.

Start a local app server, then run:

```sh
TENBIT_TEST_URL=http://127.0.0.1:8779/ npx playwright test browser_regression.spec.mjs
```
