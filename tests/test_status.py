import importlib.util
import json
from pathlib import Path
import time
import os
import tempfile
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

spec = importlib.util.spec_from_file_location("status", Path(__file__).parents[1] / "status.py")
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)


class ProgressTests(unittest.TestCase):
    def test_reports_bytes_without_inventing_file_total(self):
        p = status.Progress()
        p.consume({"MESSAGE": "INFO : 3.273 GiB / 3.273 GiB, 100%, 1.699 MiB/s, ETA 0s"})
        self.assertEqual(p.total_bytes, round(3.273 * 1024 ** 3))
        self.assertEqual(p.speed, round(1.699 * 1024 ** 2))
        self.assertIsNone(p.total_files)
        self.assertFalse(p.success)  # 100% of known work is not sync completion.

    def test_binary_journal_and_success(self):
        p = status.Progress()
        p.consume({"MESSAGE": list(b"\x1b[94mINFO : Photos/a.jpg: Copied (new)\x1b[0m\n")})
        self.assertEqual(p.completed, 1)
        self.assertEqual(p.last_file, "Photos/a.jpg")
        p.consume({"MESSAGE": "INFO : Bisync successful"})
        self.assertTrue(p.success)

    def test_multiline_file_count(self):
        p = status.Progress()
        p.consume({"MESSAGE": "Transferred: 3.000 MiB / 6.000 MiB, 50%, 1.000 MiB/s, ETA 3s\nTransferred: 2 / 4, 50%"})
        self.assertEqual((p.completed, p.total_files), (2, 4))


class DiscoveryTests(unittest.TestCase):
    def test_nested_user_service_is_not_the_sync_service(self):
        group = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/example-sync.service\n"
        self.assertEqual(status.unit_from_cgroup(group), "example-sync.service")

    def test_paths_with_spaces_and_no_secrets_in_result(self):
        job = status.parse_job(["/usr/bin/rclone", "bisync", "--config", "/a/config", "/a/My Photos", "proton:My Photos", "--rc", "--rc-pass", "SECRET"])
        self.assertEqual(job["localPath"], "/a/My Photos")
        self.assertEqual(job["remotes"], ["proton"])
        self.assertNotIn("SECRET", json.dumps(job))

    def test_standalone_scope_does_not_claim_generic_user_service(self):
        self.assertEqual(status.unit_from_cgroup("0::/user.slice/user@1000.service/app.slice/app-terminal.scope\n"), "")

    def test_non_sync_rclone_command_is_ignored(self):
        self.assertIsNone(status.parse_job(["rclone", "size", "proton:"]))

    def test_custom_config_and_explicit_rc_boolean_are_discovered(self):
        job = status.parse_job(["rclone", "sync", "/sync/files", "archive:", "--config=/example/rclone.conf", "--rc=true", "--rc-addr=127.0.0.1:5580"])
        self.assertEqual(job["configPath"], "/example/rclone.conf")
        self.assertEqual(job["rcAddress"], "127.0.0.1:5580")

    def test_xdg_config_reads_only_provider_metadata(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path) / 'rclone'
            root.mkdir()
            (root / 'rclone.conf').write_text('[archive]\ntype = drive\npassword = FAKE_PASSWORD\n', encoding='utf-8')
            environment = dict(os.environ, XDG_CONFIG_HOME=path)
            environment.pop('RCLONE_CONFIG', None)
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(status.remote_types(), {"archive": "drive"})

    def test_explicit_config_keeps_remote_names_with_spaces(self):
        with tempfile.TemporaryDirectory() as path:
            config = Path(path) / 'rclone.conf'
            config.write_text('[work archive]\ntype = onedrive\n', encoding='utf-8')
            self.assertEqual(status.remote_types(config), {"work archive": "onedrive"})

    def test_never_query_non_loopback_remote_control(self):
        with patch.object(status.urllib.request, "build_opener") as network:
            self.assertIsNone(status.rc_stats("https://example.com:5572"))
            self.assertIsNone(status.rc_stats("http://127.0.0.1@example.com:5572"))
            network.assert_not_called()

    def test_local_status_redirect_is_not_followed(self):
        hits = []

        class Target(BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *args):
                pass

        target = ThreadingHTTPServer(('127.0.0.1', 0), Target)

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(307)
                self.send_header('Location', f'http://127.0.0.1:{target.server_port}/redirected')
                self.end_headers()

            def log_message(self, *args):
                pass

        redirect = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        threads = [Thread(target=server.serve_forever, daemon=True) for server in (target, redirect)]
        for thread in threads:
            thread.start()
        try:
            self.assertIsNone(status.rc_stats(f'127.0.0.1:{redirect.server_port}'))
            self.assertEqual(hits, [])
        finally:
            for server in (target, redirect):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join()


class MonitorTests(unittest.TestCase):
    def setup_monitor(self):
        monitor = status.Monitor()
        monitor.discovered = time.monotonic()
        monitor.providers = {"proton": "protondrive", "drive": "drive"}
        self.job = status.parse_job(["rclone", "bisync", "/sync/files", "proton:"])
        self.job.update(unit="example-sync.service", invocation="first")
        monitor.jobs = {"example-sync.service": self.job}
        return monitor

    def props(self, unit, _names):
        if unit.endswith(".timer"):
            return {"ActiveState": "active"}
        return {"ActiveState": "inactive", "Result": "success", "InvocationID": "first"}

    def test_completed_job_and_unmonitored_remote_are_distinct(self):
        monitor = self.setup_monitor()
        with patch.object(status, "active_jobs", return_value={}), patch.object(status, "properties", side_effect=self.props), patch.object(status, "run", return_value=json.dumps({"MESSAGE": "INFO : Bisync successful", "__CURSOR": "one"})):
            clouds = monitor.snapshot()["clouds"]
        self.assertEqual(clouds[0]["state"], "unmonitored")
        self.assertEqual(clouds[1]["state"], "synced")

    def test_failure_overrides_old_success(self):
        monitor = self.setup_monitor()
        def failed(unit, names):
            return {**self.props(unit, names), "ActiveState": "failed", "Result": "exit-code"} if unit.endswith(".service") else self.props(unit, names)
        with patch.object(status, "active_jobs", return_value={}), patch.object(status, "properties", side_effect=failed), patch.object(status, "run", return_value=json.dumps({"MESSAGE": "INFO : Bisync successful"})):
            self.assertEqual(monitor.snapshot()["clouds"][1]["state"], "error")

    def test_zero_queue_during_scan_does_not_claim_zero_remaining(self):
        monitor = self.setup_monitor()
        self.job["rcAddress"] = "127.0.0.1:5572"
        stats = {"transfers": 0, "totalTransfers": 0, "listed": 174, "checks": 0}
        with patch.object(status, "active_jobs", return_value={"example-sync.service": self.job}), patch.object(status, "properties", side_effect=self.props), patch.object(status, "run", return_value=""), patch.object(status, "rc_stats", return_value=stats):
            cloud = monitor.snapshot()["clouds"][1]
            self.assertNotIn("remainingFiles", cloud)
            self.assertEqual(cloud["queuedFiles"], 0)
            self.assertEqual(cloud["phase"], "scanning")
            self.assertEqual(cloud["listed"], 174)

    def test_refilling_queue_is_not_a_fixed_remaining_total(self):
        monitor = self.setup_monitor()
        self.job["rcAddress"] = "127.0.0.1:5572"
        first = {"transfers": 3684, "totalTransfers": 13689, "bytes": 7000, "totalBytes": 20000, "eta": 3661, "transferring": [{"name": "Photos/a.jpg", "percentage": 67, "bytes": 67, "size": 100}]}
        second = {**first, "transfers": 3762, "totalTransfers": 13767, "bytes": 7800, "totalBytes": 20800, "eta": 3500}
        with patch.object(status, "active_jobs", return_value={"example-sync.service": self.job}), patch.object(status, "properties", side_effect=self.props), patch.object(status, "run", return_value=""), patch.object(status, "rc_stats", side_effect=[first, second]):
            a = monitor.snapshot()["clouds"][1]
            b = monitor.snapshot()["clouds"][1]
        self.assertEqual(a["queuedFiles"], b["queuedFiles"])
        self.assertGreater(b["completedFiles"], a["completedFiles"])
        self.assertEqual(a["queuedBytes"], 13000)
        self.assertEqual(a["activeFiles"][0]["percentage"], 67)
        self.assertEqual(a["eta"], "1h 1m")
        self.assertNotIn("remainingFiles", b)

    def test_new_invocation_clears_prior_counts_and_success(self):
        monitor = self.setup_monitor()
        with patch.object(status, "active_jobs", return_value={"example-sync.service": self.job}), patch.object(status, "properties", side_effect=self.props), patch.object(status, "run", return_value=json.dumps({"MESSAGE": "INFO : a.jpg: Copied (new)", "__CURSOR": "cursor-one"})):
            self.assertEqual(monitor.snapshot()["clouds"][1]["completedFiles"], 1)
        def second(unit, names):
            return {**self.props(unit, names), "InvocationID": "second"}
        with patch.object(status, "active_jobs", return_value={"example-sync.service": self.job}), patch.object(status, "properties", side_effect=second), patch.object(status, "run", return_value=""):
            self.assertEqual(monitor.snapshot()["clouds"][1]["completedFiles"], 0)

    def test_cursor_is_used_to_avoid_recounting(self):
        monitor = self.setup_monitor()
        with patch.object(status, "active_jobs", return_value={}), patch.object(status, "properties", side_effect=self.props), patch.object(status, "run", side_effect=[json.dumps({"MESSAGE": "INFO : a.jpg: Copied (new)", "__CURSOR": "one"}), ""]) as run:
            monitor.snapshot()
            result = monitor.snapshot()
            self.assertIn("--after-cursor", run.call_args.args[0])
            self.assertEqual(result["clouds"][1]["completedFiles"], 1)

    def test_verified_inventory_requires_success_and_unambiguous_workdir(self):
        monitor = status.Monitor()
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            for side in (1, 2):
                (directory / f'job.path{side}.lst').write_text('# listing\nentry1\nentry2\n')
            job = {"mode": "bisync", "workdir": path, "localPath": "/local", "paths": ["/local", "proton:"]}
            self.assertIsNone(monitor.inventory(job, None))
            self.assertEqual(monitor.inventory(job, time.time()), {"localItems": 2, "remoteItems": 2})
            for side in (1, 2):
                (directory / f'other.path{side}.lst').write_text('# listing\nentry\n')
            self.assertIsNone(monitor.inventory(job, time.time()))

    def test_inventory_from_a_different_run_is_not_shown_as_verified(self):
        monitor = status.Monitor()
        with tempfile.TemporaryDirectory() as path:
            for side in (1, 2):
                file = Path(path) / f'job.path{side}.lst'
                file.write_text('# listing\nentry\n')
                os.utime(file, (100, 100))
            self.assertIsNone(monitor.inventory({"mode": "bisync", "workdir": path, "localPath": "/local", "paths": ["/local", "proton:"]}, time.time()))


if __name__ == "__main__":
    unittest.main()
