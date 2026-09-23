#!/usr/bin/env python3
"""Contract tests for LSP-first, bounded, non-authoritative navigation."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from harnesslib.navigation import CodeNavigator
from harnesslib.state import HarnessError


class FakeLspClient:
    instances = []

    def __init__(self, command, root, timeout):
        self.command = command
        self.root = root
        self.timeout = timeout
        self.requests = []
        self.notifications = []
        self.closed = False
        self.__class__.instances.append(self)

    def initialise(self):
        return None

    def notify(self, method, params=None):
        self.notifications.append((method, params))

    def request(self, method, params=None):
        self.requests.append((method, params))
        source = (self.root / "sample.py").resolve().as_uri()
        return [
            {
                "uri": source,
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 10},
                },
            }
        ]

    def close(self):
        self.closed = True


class FailingLspClient(FakeLspClient):
    def initialise(self):
        raise TimeoutError("fixture timeout")


class CodeNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "sample.py").write_text(
            "def target(value):\n    return value\n\nresult = target(1)\n"
        )
        (self.root / "other.py").write_text(
            "targeted = 2\nresult = target(3)\n"
        )
        FakeLspClient.instances.clear()

    def navigator(self, client=FakeLspClient, max_results=40):
        return CodeNavigator(
            self.root,
            max_results=max_results,
            server_commands={".py": [[sys.executable]]},
            client_factory=client,
        )

    def test_lsp_locations_are_preferred_and_never_called_authoritative(self):
        result = self.navigator().query(
            "definition", Path("sample.py"), 1, 5, symbol="target"
        )
        self.assertEqual(result["source"], "LSP")
        self.assertFalse(result["authoritative"])
        self.assertEqual(result["coverage"], "SERVER_REPORTED")
        self.assertEqual(result["locations"][0]["path"], "sample.py")
        client = FakeLspClient.instances[-1]
        self.assertTrue(client.closed)
        method, params = client.requests[-1]
        self.assertEqual(method, "textDocument/definition")
        self.assertEqual(params["position"], {"line": 0, "character": 4})

    def test_real_stdio_client_speaks_lsp_framing(self):
        server = self.root / "fake_lsp.py"
        server.write_text(
            """import json
import sys

uri = None

def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line == b"\\r\\n":
            break
        key, value = line.decode().split(":", 1)
        headers[key.lower()] = value.strip()
    return json.loads(sys.stdin.buffer.read(int(headers["content-length"])))

def send(value):
    body = json.dumps(value, separators=(",", ":")).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode() + body)
    sys.stdout.buffer.flush()

while True:
    message = read_message()
    if message is None:
        break
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
    elif method == "textDocument/didOpen":
        uri = message["params"]["textDocument"]["uri"]
    elif method == "textDocument/definition":
        send({"jsonrpc": "2.0", "id": message["id"], "result": [{
            "uri": uri,
            "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 10}}
        }]})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif method == "exit":
        break
"""
        )
        navigator = CodeNavigator(
            self.root,
            server_commands={".py": [[sys.executable, str(server)]]},
        )
        result = navigator.query(
            "definition", Path("sample.py"), 1, 5, symbol="target"
        )
        self.assertEqual(result["source"], "LSP")
        self.assertEqual(result["locations"][0]["line"], 1)

    def test_text_fallback_is_exact_bounded_and_explicitly_incomplete(self):
        navigator = CodeNavigator(
            self.root, max_results=2, server_commands={".py": []}
        )
        result = navigator.query(
            "references", Path("sample.py"), 1, 5, symbol="target"
        )
        self.assertEqual(result["source"], "TEXT_FALLBACK")
        self.assertFalse(result["authoritative"])
        self.assertEqual(result["coverage"], "TEXT_OCCURRENCES_ONLY")
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["locations"]), 2)
        self.assertNotIn("targeted", json.dumps(result))

    def test_failed_lsp_falls_back_and_reports_bounded_diagnostic(self):
        result = self.navigator(FailingLspClient).query(
            "definition", Path("sample.py"), 1, 5, symbol="target"
        )
        self.assertEqual(result["source"], "TEXT_FALLBACK")
        self.assertIn("fixture timeout", result["lsp_error"])
        self.assertTrue(FailingLspClient.instances[-1].closed)

    def test_paths_outside_root_fail_closed(self):
        outside = self.root.parent / "outside.py"
        outside.write_text("target = 1\n")
        self.addCleanup(outside.unlink)
        with self.assertRaisesRegex(HarnessError, "outside the project root"):
            self.navigator().query("definition", outside, 1, 1, symbol="target")

    def test_invalid_source_position_fails_before_server_dispatch(self):
        with self.assertRaisesRegex(HarnessError, "line is outside"):
            self.navigator().query(
                "definition", Path("sample.py"), 99, 1, symbol="target"
            )
        self.assertEqual(FakeLspClient.instances, [])

    def test_cli_returns_locations_only_without_source_excerpts(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "code-nav.py"),
                "--root",
                str(self.root),
                "references",
                "--file",
                "sample.py",
                "--line",
                "1",
                "--column",
                "5",
                "--symbol",
                "target",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["source"], "TEXT_FALLBACK")
        self.assertNotIn("content", result)
        self.assertNotIn("excerpt", result)

    def test_mechanical_agents_use_adapter_without_graft(self):
        for name in (
            "mechanical-controller.md",
            "mechanical-worker.md",
            "mechanical-verifier.md",
            "mechanical-reviewer.md",
        ):
            text = (ROOT / "agents" / name).read_text()
            self.assertIn("references/code-navigation.md", text)
        reference = (ROOT / "skills" / "implement" / "references" / "code-navigation.md").read_text()
        self.assertIn("authoritative` is always false", reference)
        self.assertIn("Do not", reference)
        self.assertIn("Graft", reference)


if __name__ == "__main__":
    unittest.main()
