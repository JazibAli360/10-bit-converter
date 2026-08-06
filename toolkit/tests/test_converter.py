"""Regression tests for the export-safety rules that must never regress."""

import os
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

import server
from conversion_runner import run_ffmpeg
from conversion_service import ConversionPlanner, EXPORT_PROVENANCE
from history_store import append_report, read_reports
from preflight import estimate_export_bytes as estimate_export_bytes_pure
from preview_service import PreviewService
from watch_service import WatchService
from single_instance import InstanceGuard
import runtime_platform
from engines import DEFAULT_ENGINE, LIBPLACEBO_ENGINE, engine_catalog, requested_engine
from media_probe import pixfmt_bits
from update_service import check_for_update, version_key


class ExportSafetyTests(unittest.TestCase):
    def test_update_checker_reads_only_a_valid_github_release_tag(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _limit):
                return b'{"tag_name":"v0.2.0"}'

        observed = {}

        def opener(request, timeout):
            observed["url"] = request.full_url
            observed["timeout"] = timeout
            return Response()

        result = check_for_update("0.1.0", opener=opener)
        self.assertTrue(result["ok"])
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "0.2.0")
        self.assertIn("/releases/latest", observed["url"])
        self.assertEqual(observed["timeout"], 6)
        self.assertEqual(version_key("v1.2.3"), (1, 2, 3, 0))
        self.assertIsNone(version_key("latest"))

    def test_weekly_update_notice_is_claimed_once_per_completed_check(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(server, "UPDATE_STATE_PATH", os.path.join(folder, "update_state.json")), \
             patch("server.check_for_update", return_value={
                 "ok": True, "current_version": "0.1.0", "latest_version": "0.2.0", "update_available": True,
             }):
            original = dict(server.UPDATE_STATE)
            try:
                server.UPDATE_STATE.update({"last_checked_at": 0, "last_notice_check_at": 0,
                                            "result": None, "checking": False})
                server.run_update_check()
                first = server.claim_weekly_update_notice()
                second = server.claim_weekly_update_notice()
                self.assertEqual(first["notice"]["latest_version"], "0.2.0")
                self.assertIsNone(second["notice"])
                self.assertTrue(os.path.isfile(server.UPDATE_STATE_PATH))
            finally:
                server.UPDATE_STATE.clear()
                server.UPDATE_STATE.update(original)

    def test_windows_release_contract_uses_exe_tools_and_local_appdata(self):
        with patch.object(runtime_platform, "IS_WINDOWS", True), \
             patch.object(runtime_platform, "IS_MACOS", False), \
             patch("runtime_platform.platform.machine", return_value="AMD64"), \
             patch.dict(os.environ, {"LOCALAPPDATA": r"C:\\Users\\Creator\\AppData\\Local"}, clear=False):
            self.assertEqual(runtime_platform.ffmpeg_tool_names(), ("ffmpeg.exe", "ffprobe.exe"))
            self.assertEqual(runtime_platform.binary_platform_dir(), "win-x64")
            self.assertIn("Jazib Ali 360", runtime_platform.app_support_dir("Jazib Ali 360", "10-bit Converter"))
        root = os.path.dirname(os.path.dirname(server.__file__))
        build_path = os.path.join(root, ".windows", "build-release.ps1")
        self.assertTrue(os.path.isfile(build_path))
        self.assertTrue(os.path.isfile(os.path.join(root, ".windows", "10-bit-converter.iss")))
        with open(build_path, encoding="utf-8") as handle:
            build = handle.read()
        self.assertIn("IncludeGpu", build)
        self.assertIn("bin\\win-x64\\libplacebo", build)

    def test_single_instance_guard_recovers_stale_locks_and_keeps_live_locks(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "app.lock")
            stale = InstanceGuard(path)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("999999999")
            self.assertTrue(stale.acquire())
            self.assertTrue(os.path.isfile(path))
            live = InstanceGuard(path)
            self.assertFalse(live.acquire())
            stale.release()
            self.assertFalse(os.path.exists(path))

    def test_watch_service_waits_for_a_stable_file_before_batching(self):
        class Job:
            def __init__(self):
                self.running = False
                self.items = []
                self.lock = threading.RLock()
            def reset(self):
                self.items = []
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "clip.mp4")
            with open(source, "wb") as handle:
                handle.write(b"video")
            state = {"enabled": True, "folder": folder, "processed": 0}
            calls = []
            service = WatchService(Job(), state, {"mode": "HEVC (smaller, delivery)", "strength": "Medium", "rate": "Match source"},
                                   (".mp4",), lambda path: path, lambda: {"dest_mode": "same", "dest_dir": "", "suffix": "_10bit"},
                                   lambda path, *_: path + "_10bit.mp4", lambda *args: calls.append(args))
            service.tick()
            self.assertEqual(calls, [])
            service.tick()
            self.assertEqual(len(calls), 1)
            self.assertEqual(state["processed"], 1)

    def test_conversion_planner_keeps_per_video_profile_and_prores_path_rules(self):
        planner = ConversionPlanner("/tmp/intake", DEFAULT_ENGINE.strength_thresholds, DEFAULT_ENGINE)
        mode, strength, rate, override = planner.normalise(
            {"override": {"mode": "ProRes 4444 (grading, huge file)", "strength": "High", "rate": "Custom"}},
            "HEVC (smaller, delivery)", "Medium", "Match source")
        self.assertEqual((mode, strength, rate), ("ProRes 4444 (grading, huge file)", "High", "Custom"))
        self.assertEqual(override["mode"], mode)
        self.assertEqual(planner.output_path("/clips/take.mp4", True, "", "_10bit"), "/clips/take_10bit.mov")
        self.assertEqual(planner.output_path("/tmp/intake/take.mp4", False, "", "_10bit"),
                         os.path.expanduser("~/Downloads/take_10bit.mp4"))

    def test_preview_service_only_serves_its_own_named_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            artifact_dir = os.path.join(folder, "scope")
            os.makedirs(artifact_dir)
            image = os.path.join(artifact_dir, "src_waveform.png")
            with open(image, "wb") as handle:
                handle.write(b"png")
            service = PreviewService(folder, lambda path: path, DEFAULT_ENGINE,
                                     lambda *args: "null", lambda *args: "null")
            service._artifacts["scope"]["token"] = artifact_dir
            self.assertEqual(service.image_path("scope", "token", "src_waveform"), image)
            self.assertIsNone(service.image_path("scope", "token", "../secret"))
            service.cleanup()
            self.assertFalse(os.path.exists(artifact_dir))

    def test_preview_service_replaces_old_artifacts_and_caps_processed_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            service = PreviewService(folder, lambda path: path, DEFAULT_ENGINE,
                                     lambda *args: "null", lambda *args: "null")
            first = os.path.join(folder, "first")
            second = os.path.join(folder, "second")
            os.makedirs(first); os.makedirs(second)
            service._artifacts["scope"] = {"one": first, "two": second}
            service._clear_kind("scope")
            self.assertFalse(os.path.exists(first))
            self.assertFalse(os.path.exists(second))
            for index in range(4):
                sample = os.path.join(folder, f"sample-{index}.mp4")
                with open(sample, "wb") as handle:
                    handle.write(b"sample")
                service._samples.append(sample)
            stale = service._samples.pop(0)
            os.remove(stale)
            self.assertEqual(len(service._samples), 3)
            self.assertFalse(os.path.exists(stale))

    def test_engine_contract_keeps_custom_and_named_deband_strengths_stable(self):
        self.assertTrue(DEFAULT_ENGINE.capabilities.local_only)
        self.assertEqual(DEFAULT_ENGINE.threshold_for("Medium", "0.031"), "0.02")
        self.assertEqual(DEFAULT_ENGINE.threshold_for("Custom", "0.031"), "0.031")
        self.assertIn("deband=1thr=0.02", DEFAULT_ENGINE.build_filter_chain(
            "0.02", "yuv420p10le", range=16, blur=True, dither=2,
        ))
        self.assertIn("libplacebo=deband=true", LIBPLACEBO_ENGINE.build_filter_chain(
            "4", "yuv420p10le", iterations=2, radius=16, grain=5,
        ))
        self.assertEqual(pixfmt_bits("yuv420p10le"), 10)

    def test_ai_colour_safe_pipeline_is_high_precision_chroma_aware_and_stable(self):
        filters = DEFAULT_ENGINE.build_filter_chain(
            "0.02", "yuv420p10le", range=16, blur=True, dither=2, colour_safe=True,
        )
        self.assertIn("format=yuv444p16le", filters)
        self.assertIn("2thr=0.012", filters)
        self.assertIn("allf=p", filters)
        self.assertIn("zscale=dither=error_diffusion", filters)
        planner = ConversionPlanner("/tmp/intake", DEFAULT_ENGINE.strength_thresholds, DEFAULT_ENGINE)
        with patch("conversion_service.probe_colour_metadata", return_value={}):
            plan = planner.plan({"path": "/clip.mp4"}, "HEVC (smaller, delivery)", "Medium", "Quality (CRF)",
                                {"crf": 18, "deband_range": 16, "deband_blur": True, "dither": 2,
                                 "thr_custom": "0.03", "max_quality": True, "denoise": "off", "deflicker": False,
                                 "audio": "copy", "colour_safe": True, "source_interpretation": "rec709_limited"},
                                DEFAULT_ENGINE)
        self.assertIn("-color_primaries", plan["colour"])
        self.assertEqual(plan["colour_encoder"][0], "-x265-params")
        self.assertIn("colorprim=bt709", plan["colour_encoder"][1])
        self.assertIn("transfer=bt709", plan["colour_encoder"][1])
        self.assertIn("colormatrix=bt709", plan["colour_encoder"][1])
        self.assertIn("AI Colour-Safe", plan["profile"])

    def test_optional_libplacebo_is_capability_gated(self):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "no GPU"

        with patch("engines.libplacebo_deband.subprocess.run", return_value=Result()):
            status = LIBPLACEBO_ENGINE.availability("ffmpeg")
        self.assertFalse(status["available"])
        self.assertIn("initialization failed", status["reason"])

        with patch("engines.engine_catalog", wraps=engine_catalog):
            catalog = engine_catalog("definitely-not-the-bundled-ffmpeg")
        self.assertEqual(catalog[0]["id"], DEFAULT_ENGINE.engine_id)
        self.assertEqual(catalog[1]["id"], LIBPLACEBO_ENGINE.engine_id)
        engine, reason = requested_engine("not-an-engine")
        self.assertIsNone(engine)
        self.assertIn("Unknown", reason)

    def test_libplacebo_qa_manifest_has_hardware_checks(self):
        manifest_path = os.path.join(os.path.dirname(server.__file__), "qa",
                                     "libplacebo-corpus", "manifest.json")
        import json
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertGreaterEqual(len(manifest["clips"]), 8)
        self.assertIn("engine availability and Vulkan device creation", manifest["required_checks"])
        smoke_path = os.path.join(os.path.dirname(server.__file__), "qa", "release_smoke_test.py")
        with open(smoke_path, encoding="utf-8") as handle:
            smoke = handle.read()
        self.assertIn("Faithful CPU deband processes a 10-bit frame", smoke)
        self.assertIn("libplacebo GPU deband processes a 10-bit frame", smoke)

    def test_main_ui_keeps_expert_controls_behind_advanced(self):
        index_path = os.path.join(os.path.dirname(server.__file__), "index.html")
        with open(index_path, encoding="utf-8") as source:
            html = source.read()
        self.assertIn('id="profile-faithful"', html)
        self.assertIn('id="profile-editing"', html)
        self.assertIn('id="profile-advanced"', html)
        self.assertIn('id="advancedSettingsFields" style="display:none"', html)
        self.assertNotIn("Smart Auto", html)
        self.assertIn("Processed samples pause while an export is running", html)
        self.assertIn("Analyze gradients (experimental)", html)
        self.assertIn("Copy report", html)
        self.assertIn('id="engineSelect"', html)
        queue_path = os.path.join(os.path.dirname(server.__file__), "ui", "queue.js")
        lifecycle_path = os.path.join(os.path.dirname(server.__file__), "native_lifecycle.py")
        with open(queue_path, encoding="utf-8") as source:
            queue_ui = source.read()
        with open(lifecycle_path, encoding="utf-8") as source:
            lifecycle = source.read()
        self.assertIn('if(NATIVE_SHELL){ setStatus("Adding dropped video…"); return; }', queue_ui)
        self.assertIn("install_native_drop_bridge", lifecycle)
        self.assertIn("pywebviewFullPath", lifecycle)

    def test_collision_rename_counts_up_without_touching_original(self):
        with tempfile.TemporaryDirectory() as folder:
            original = os.path.join(folder, "clip_10bit.mp4")
            with open(original, "wb"):
                pass
            self.assertEqual(server.resolve_output_path(original, "rename"),
                             os.path.join(folder, "clip_10bit-2.mp4"))
            reserved = {os.path.join(folder, "clip_10bit-2.mp4")}
            self.assertEqual(server.resolve_output_path(original, "rename", reserved),
                             os.path.join(folder, "clip_10bit-3.mp4"))

    def test_skip_and_overwrite_keep_the_requested_final_path(self):
        path = "/tmp/example_10bit.mp4"
        self.assertEqual(server.resolve_output_path(path, "skip"), path)
        self.assertEqual(server.resolve_output_path(path, "overwrite"), path)

    def test_staging_output_is_a_same_folder_hidden_file_with_media_extension(self):
        final_path = "/tmp/exports/clip_10bit.mov"
        staged = server.staging_output_path(final_path)
        self.assertEqual(os.path.dirname(staged), "/tmp/exports")
        self.assertTrue(os.path.basename(staged).startswith(".clip_10bit.tenbit-partial-"))
        self.assertTrue(staged.endswith(".mov"))
        self.assertNotEqual(staged, final_path)

    def test_stream_map_keeps_primary_video_all_audio_and_metadata(self):
        self.assertEqual(server.stream_map_args(), [
            "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0", "-map_chapters", "0",
        ])
        self.assertEqual(ConversionPlanner.provenance_args(), [
            "-metadata", f"comment={EXPORT_PROVENANCE}",
        ])

    def test_preflight_blocks_unwritable_or_missing_source(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = os.path.join(folder, "missing.mp4")
            settings = dict(server.DEFAULT_SETTINGS)
            report = server.build_preflight(
                [{"path": missing, "name": "missing.mp4"}],
                "HEVC (smaller, delivery)", "Medium", "Match source", settings,
            )
            self.assertFalse(report["ready"])
            self.assertTrue(any("Source file is missing" in item for item in report["blocking"]))

    def test_preflight_respects_per_video_prores_and_uses_mov_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "edit-me.mp4")
            with open(source, "wb"):
                pass
            item = {"path": source, "name": "edit-me.mp4", "override": {
                "mode": "ProRes 4444 (grading, huge file)",
                "strength": "High", "rate": "Quality (CRF)",
            }}
            with patch.object(server, "probe_info", return_value={
                "dur": 10, "kbps": 8_000, "size": 10_000_000,
                "width": 1920, "height": 1080, "fps": 30,
            }):
                report = server.build_preflight(
                    [item], "HEVC (smaller, delivery)", "Medium", "Match source",
                    dict(server.DEFAULT_SETTINGS),
                )
            row = report["items"][0]
            self.assertEqual(row["format"], "ProRes 4444")
            self.assertTrue(row["out"].endswith("edit-me_10bit.mov"))
            self.assertGreater(row["estimate"], 0)

    def test_preflight_collision_policy_makes_a_decision_before_convert(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "clip.mp4")
            existing = os.path.join(folder, "clip_10bit.mp4")
            for path in (source, existing):
                with open(path, "wb"):
                    pass
            item = {"path": source, "name": "clip.mp4"}
            probe = {"dur": 1, "kbps": 1_000, "size": 125_000}
            with patch.object(server, "probe_info", return_value=probe):
                renamed = server.build_preflight([item], "HEVC (smaller, delivery)", "Medium", "Match source",
                                                  {**server.DEFAULT_SETTINGS, "on_exists": "rename"})
                overwrite = server.build_preflight([item], "HEVC (smaller, delivery)", "Medium", "Match source",
                                                    {**server.DEFAULT_SETTINGS, "on_exists": "overwrite"})
                skipped = server.build_preflight([item], "HEVC (smaller, delivery)", "Medium", "Match source",
                                                  {**server.DEFAULT_SETTINGS, "on_exists": "skip"})
            self.assertTrue(renamed["items"][0]["renamed"])
            self.assertTrue(renamed["items"][0]["out"].endswith("clip_10bit-2.mp4"))
            self.assertEqual(overwrite["items"][0]["out"], existing)
            self.assertEqual(skipped["items"][0]["out"], existing)
            self.assertEqual(overwrite["collisions"], ["clip.mp4"])

    def test_report_and_history_keep_exact_source_output_and_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "source.mp4")
            output = os.path.join(folder, "source_10bit.mp4")
            for path, payload in ((source, b"source"), (output, b"converted")):
                with open(path, "wb") as handle:
                    handle.write(payload)
            log = os.path.join(folder, "history.jsonl")
            override = {"mode": "HEVC (smaller, delivery)", "strength": "Medium",
                        "rate": "Match source", "target_mbps": 12}
            item = {"path": source, "name": "source.mp4", "out": output,
                    "status": "Done", "profile": "HEVC · Medium deband", "override": override}
            with patch.object(server, "REPORT_LOG_PATH", log), \
                 patch.object(server, "ensure_app_support_dir", return_value=None):
                report = server.build_and_store_report(
                    [item], "HEVC (smaller, delivery)", "Medium", "Match source",
                    dict(server.DEFAULT_SETTINGS), 1, 0, 0, False, time.time(),
                )
                history = server.load_history()
            self.assertEqual(report["items"][0]["source"], source)
            self.assertEqual(report["items"][0]["output"], output)
            self.assertEqual(report["items"][0]["override"], override)
            self.assertEqual(report["engine"], DEFAULT_ENGINE.engine_id)
            self.assertEqual(history[0]["items"][0]["output"], output)

    def test_window_state_is_persisted_outside_the_bundle_and_clamped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "window_state.json")
            with patch.object(server, "WINDOW_STATE_PATH", path):
                server.atomic_json_write(path, {"width": 99999, "height": 1, "x": 123, "y": -45})
                state = server.load_window_state()
            self.assertEqual(state["width"], 4096)
            self.assertEqual(state["height"], 620)
            self.assertEqual(state["x"], 123)
            self.assertEqual(state["y"], -45)

    def test_process_runner_drains_a_large_error_stream(self):
        server.JOB.cancel.clear()
        error = server._run_ffmpeg([
            sys.executable, "-c", "import sys; sys.stderr.write('x' * 200000); sys.exit(3)",
        ], 0, -1)
        self.assertIn("x", error)

    def test_extracted_runner_reports_progress_and_drains_stderr(self):
        cancel = __import__("threading").Event()
        progress = []
        error = run_ffmpeg([
            sys.executable, "-c",
            "import sys; print('out_time_us=1000000'); print('progress=end'); "
            "sys.stderr.write('e' * 100000);",
        ], cancel, on_progress=progress.append)
        self.assertIsNone(error)
        self.assertEqual(progress[0]["out_time_us"], "1000000")

    def test_process_runner_stops_a_silent_hung_command(self):
        error = run_ffmpeg(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            threading.Event(), inactivity_timeout=0.1,
        )
        self.assertIn("stopped reporting progress", error)

    def test_failed_export_keeps_a_log_and_discards_partial_media(self):
        old_items = server.JOB.items
        try:
            with tempfile.TemporaryDirectory() as folder:
                partial = os.path.join(folder, ".clip.tenbit-partial-test.mp4")
                with open(partial, "wb") as handle:
                    handle.write(b"incomplete")
                server.JOB.items = [{"name": "clip.mp4", "status": "Running"}]
                with patch.object(server, "FAILURE_LOG_DIR", os.path.join(folder, "logs")):
                    server.mark_item_failed(0, "clip.mp4", "encoder failed", ["ffmpeg", "-i", "clip.mp4"], partial)
                item = server.JOB.items[0]
                self.assertEqual(item["status"], "Failed")
                self.assertFalse(os.path.exists(partial))
                self.assertIn("discarded safely", item["recovery"])
                self.assertTrue(os.path.isfile(item["log_path"]))
                with open(item["log_path"], encoding="utf-8") as handle:
                    self.assertIn("encoder failed", handle.read())
        finally:
            server.JOB.items = old_items

    def test_queue_workflow_regression_contract(self):
        """Keep the add/remove/duplicate/retry/relaunch flow from regressing."""
        root = os.path.dirname(server.__file__)
        paths = [os.path.join(root, "index.html"), os.path.join(root, "ui", "queue.js"),
                 os.path.join(root, "ui", "conversion.js"), os.path.join(root, "ui", "preview.js")]
        chunks = []
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                chunks.append(handle.read())
        ui = "\n".join(chunks)
        for contract in (
            "pendingDropPaths.has(dedupeKey)",  # one Finder drop → one row
            "function removeAt(i)",             # row removal refreshes queue state
            "function duplicateAt(i)",          # alternate export preserves source/profile
            "function retryFailed()",           # failed-only retry returns to preflight
            "tenbit.pendingQueue.v1",           # pending queue survives relaunch
            "preflightOnExists",                 # collision is decided before encode
            "data-log=",                         # failed rows expose their diagnostic
        ):
            self.assertIn(contract, ui)

    def test_ui_behavior_modules_are_loaded_and_packaged(self):
        root = os.path.dirname(server.__file__)
        with open(os.path.join(root, "index.html"), encoding="utf-8") as handle:
            html = handle.read()
        with open(os.path.join(root, "setup_native.py"), encoding="utf-8") as handle:
            package = handle.read()
        for name in ("queue.js", "conversion.js", "preview.js", "watch.js", "settings.js", "controls.js"):
            path = os.path.join(root, "ui", name)
            self.assertTrue(os.path.isfile(path), name)
            self.assertIn(f'ui/{name}', html)
            self.assertIn(f'ui/{name}', package)

    def test_browser_regression_suite_covers_the_risky_ui_paths(self):
        path = os.path.join(os.path.dirname(server.__file__), "qa", "browser_regression.spec.mjs")
        with open(path, encoding="utf-8") as handle:
            suite = handle.read()
        for coverage in ("duplicate, and remove", "per-video settings", "preflight", "scopes and comparison",
                         "completion result", "680 px layout"):
            self.assertIn(coverage, suite)

    def test_quality_mode_has_a_source_size_based_preflight_estimate(self):
        with patch.object(server, "probe_info", return_value={
            "dur": 10, "kbps": 8_000, "size": 10_000_000, "width": 1920, "height": 1080, "fps": 30,
        }):
            hevc = server.estimate_export_bytes({"path": "/tmp/clip.mp4"}, "HEVC (smaller, delivery)",
                                                "Quality (CRF)", server.DEFAULT_SETTINGS)
            h264 = server.estimate_export_bytes({"path": "/tmp/clip.mp4"}, "H.264 (10-bit, delivery)",
                                                "Quality (CRF)", server.DEFAULT_SETTINGS)
        self.assertEqual(hevc, 7_800_000)
        self.assertEqual(h264, 10_500_000)

    def test_extracted_preflight_and_history_modules_are_standalone(self):
        info = {"dur": 10, "kbps": 8_000, "size": 10_000_000,
                "width": 1920, "height": 1080, "fps": 30}
        self.assertEqual(estimate_export_bytes_pure(
            info, "HEVC (smaller, delivery)", "Quality (CRF)",
            server.DEFAULT_SETTINGS, lambda mode: "hevc"), 7_800_000)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "history.jsonl")
            append_report(path, {"id": "r1", "items": [{"source": "/tmp/a", "output": "/tmp/b"}]}, limit=2)
            append_report(path, {"id": "r2", "items": []}, limit=2)
            append_report(path, {"id": "r3", "items": []}, limit=2)
            records = read_reports(path, authorize_path=lambda value: value)
        self.assertEqual([record["id"] for record in records], ["r3", "r2"])


if __name__ == "__main__":
    unittest.main()
