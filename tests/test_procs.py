import helpers
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from unhoard import enrich, procs


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a killed orphan can linger as a zombie until its new parent reaps it
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except FileNotFoundError:
        if Path("/proc/self").exists():
            return False  # gone between the two checks
    except OSError:
        return False
    # no /proc (macOS): ask ps for the state
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def wait_dead(pid, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


# a program that starts a background grandchild, records its pid, then waits (so the timeout fires)
SPAWNER = "import subprocess, sys, time; p = subprocess.Popen(['sleep', '120']); open(sys.argv[1], 'w').write(str(p.pid)); time.sleep(120)"


class ProcessGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pidfile = str(Path(self.tmp.name) / "pid")

    def grandchild(self):
        end = time.time() + 5
        while time.time() < end and not Path(self.pidfile).exists():
            time.sleep(0.02)
        time.sleep(0.05)
        return int(Path(self.pidfile).read_text())

    def test_timeout_kills_the_whole_group(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            procs.run_group([sys.executable, "-c", SPAWNER, self.pidfile], None, timeout=1.0)
        pid = self.grandchild()
        self.assertTrue(wait_dead(pid), "the grandchild survived the timeout")

    def test_plain_subprocess_run_would_leave_the_grandchild_running(self):
        """The reason for run_group: this is the old behaviour."""
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run([sys.executable, "-c", SPAWNER, self.pidfile], capture_output=True, timeout=1.0)
        pid = self.grandchild()
        self.addCleanup(lambda: os.kill(pid, 9) if alive(pid) else None)
        self.assertTrue(alive(pid))

    def test_normal_run_returns_output_and_code(self):
        done = procs.run_group([sys.executable, "-c", "import sys; print(sys.stdin.read().upper()); sys.exit(3)"], "abc", 10)
        self.assertEqual((done.returncode, done.stdout.strip()), (3, "ABC"))

    def test_the_child_leads_its_own_session(self):
        done = procs.run_group([sys.executable, "-c", "import os; print(os.getpgrp() == os.getpid())"], None, 10)
        self.assertEqual(done.stdout.strip(), "True")

    def test_run_agent_timeout_kills_the_group(self):
        with self.assertRaises(enrich.EnrichmentError) as ctx:
            enrich.run_agent([sys.executable, "-c", SPAWNER, self.pidfile], "x", 1)
        self.assertIn("timed out", str(ctx.exception))
        self.assertTrue(wait_dead(self.grandchild()))

    def test_context_command_timeout_kills_the_group(self):
        text = enrich._run_context_command([sys.executable, "-c", SPAWNER, self.pidfile], 1, None, 1000)
        self.assertIn("timed out", text)
        self.assertTrue(wait_dead(self.grandchild()))

    def test_interruption_kills_the_group(self):
        """Ctrl-C (KeyboardInterrupt) while waiting for the program must not leave its helpers behind."""
        from unittest import mock
        pidfile = self.pidfile

        def interrupted(popen, *args, **kwargs):
            end = time.time() + 5
            while time.time() < end and not Path(pidfile).exists():
                time.sleep(0.02)
            raise KeyboardInterrupt

        with mock.patch.object(subprocess.Popen, "communicate", interrupted), self.assertRaises(KeyboardInterrupt):
            procs.run_group([sys.executable, "-c", SPAWNER, pidfile], None, timeout=60)
        self.assertTrue(wait_dead(self.grandchild()))

    def test_timeout_is_bounded_even_when_a_grandchild_holds_the_pipe(self):
        """A helper that left the group (setsid) keeps stdout open; the timeout must not wait for it."""
        script = ("import subprocess, sys, time; p = subprocess.Popen(['sleep', '8'], start_new_session=True); "
                  "open(sys.argv[1], 'w').write(str(p.pid)); time.sleep(60)")
        started = time.monotonic()
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                procs.run_group([sys.executable, "-c", script, self.pidfile], None, timeout=1.0)
            elapsed = time.monotonic() - started
        finally:
            pid = self.grandchild()
            self.addCleanup(lambda: os.kill(pid, 9) if alive(pid) else None)
        self.assertLess(elapsed, 3.0, "the timeout waited for the escaped grandchild")

    def test_sigterm_and_sighup_kill_the_agents_group(self):
        """A supervisor's kill of the program must not leave the agent's helpers running."""
        import signal
        for signum in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum.name):
                pidfile = str(Path(self.tmp.name) / f"pid-{signum.name}")
                runner = ("import sys; sys.path.insert(0, sys.argv[3]); from unhoard import procs; "
                          "procs.run_group([sys.executable, '-c', sys.argv[1], sys.argv[2]], None, timeout=60)")
                src = str(Path(helpers.ROOT) / "src")
                proc = subprocess.Popen([sys.executable, "-c", runner, SPAWNER, pidfile, src])
                self.addCleanup(lambda p=proc: p.kill() if p.poll() is None else None)
                end = time.time() + 10
                while time.time() < end and not Path(pidfile).exists():
                    time.sleep(0.02)
                time.sleep(0.2)
                pid = int(Path(pidfile).read_text())
                self.addCleanup(lambda pid=pid: os.kill(pid, 9) if alive(pid) else None)
                proc.send_signal(signum)
                proc.wait(timeout=10)
                self.assertEqual(proc.returncode, 128 + signum)  # unwound with SystemExit(128 + n)
                self.assertTrue(wait_dead(pid), "the agent's helper survived the signal")

    def test_signal_handlers_are_restored_and_only_set_on_the_main_thread(self):
        import signal
        import threading
        before = signal.getsignal(signal.SIGTERM)
        with procs.exit_on_stop_signals():
            self.assertIsNot(signal.getsignal(signal.SIGTERM), before)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)
        seen = []

        def other_thread():
            with procs.exit_on_stop_signals():
                seen.append(signal.getsignal(signal.SIGTERM))
        t = threading.Thread(target=other_thread)
        t.start()
        t.join()
        self.assertEqual(seen, [before])  # a worker thread cannot install handlers and does not try

    def test_an_ignored_signal_stays_ignored(self):
        """nohup leaves SIGHUP ignored; a run under it must not die on hangup."""
        import signal
        from unhoard import mcp
        for name, cm in (("run_group", procs.exit_on_stop_signals), ("mcp", lambda: mcp._cleanup_on_signals(self.tmp.name))):
            before = signal.signal(signal.SIGHUP, signal.SIG_IGN)
            try:
                with cm():
                    self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_IGN, name)
                    self.assertIsNot(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)  # others are still handled
                self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_IGN, name)
            finally:
                signal.signal(signal.SIGHUP, before)

    def test_a_nohup_run_survives_hangup_end_to_end(self):
        import signal
        runner = ("import os, signal, sys, threading; sys.path.insert(0, sys.argv[1]); "
                  "from unhoard import procs; signal.signal(signal.SIGHUP, signal.SIG_IGN); "
                  "threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGHUP)).start(); "
                  "r = procs.run_group(['sleep', '1'], None, timeout=10); print('done', r.returncode)")
        done = subprocess.run([sys.executable, "-c", runner, str(Path(helpers.ROOT) / "src")], capture_output=True, text=True,
                              timeout=30)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "done 0"), done.stderr)

    def test_without_process_groups_it_degrades_to_killing_the_child(self):
        from unittest import mock
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        with mock.patch.object(procs.os, "killpg", None):
            procs.kill_group(proc)
        proc.wait(timeout=5)
        self.assertIsNotNone(proc.returncode)
        with mock.patch.object(procs, "_STOP_SIGNALS", ()), mock.patch.object(procs.signal, "SIGKILL", None, create=True):
            with procs.exit_on_stop_signals():
                pass  # no SIGHUP/SIGTERM on this platform: nothing to install, nothing raised

    def test_custom_relate_and_read_use_the_same_runner(self):
        # the presets and custom commands both go through run_agent, so one fix covers every agent
        import inspect
        self.assertIn("procs.run_group", inspect.getsource(enrich.run_agent))
        self.assertIn("procs.run_group", inspect.getsource(enrich._run_context_command))


if __name__ == "__main__":
    unittest.main()
