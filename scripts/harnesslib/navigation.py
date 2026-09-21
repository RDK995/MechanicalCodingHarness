"""Bounded code-intelligence navigation with an explicit text fallback."""

from __future__ import annotations

from contextlib import suppress
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import subprocess
import time
from typing import Callable
from urllib.parse import unquote, urlparse

from .state import HarnessError


LANGUAGES = {
    ".py": (
        "python",
        (
            ("basedpyright-langserver", "--stdio"),
            ("pyright-langserver", "--stdio"),
            ("pylsp",),
        ),
    ),
    ".pyi": (
        "python",
        (
            ("basedpyright-langserver", "--stdio"),
            ("pyright-langserver", "--stdio"),
            ("pylsp",),
        ),
    ),
    ".js": ("javascript", (("typescript-language-server", "--stdio"),)),
    ".jsx": ("javascriptreact", (("typescript-language-server", "--stdio"),)),
    ".ts": ("typescript", (("typescript-language-server", "--stdio"),)),
    ".tsx": ("typescriptreact", (("typescript-language-server", "--stdio"),)),
    ".rs": ("rust", (("rust-analyzer",),)),
    ".go": ("go", (("gopls", "serve"),)),
    ".c": ("c", (("clangd",),)),
    ".h": ("c", (("clangd",),)),
    ".cc": ("cpp", (("clangd",),)),
    ".cpp": ("cpp", (("clangd",),)),
    ".cxx": ("cpp", (("clangd",),)),
    ".hpp": ("cpp", (("clangd",),)),
    ".rb": ("ruby", (("solargraph", "stdio"),)),
}
IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
MAX_LSP_MESSAGE_BYTES = 8 * 1024 * 1024


class LspClient:
    """Small synchronous JSON-RPC client for definition/reference requests."""

    def __init__(self, command: list[str], root: Path, timeout: float) -> None:
        self.command = command
        self.root = root
        self.timeout = timeout
        self.sequence = 0
        self.buffer = b""
        self.process = subprocess.Popen(
            command,
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise HarnessError("language server did not expose stdio")
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def _send(self, message: dict) -> None:
        body = json.dumps(message, separators=(",", ":")).encode()
        payload = f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        assert self.process.stdin is not None
        self.process.stdin.write(payload)
        self.process.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def _read_more(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self.selector.select(remaining):
            raise TimeoutError("language server response timed out")
        assert self.process.stdout is not None
        chunk = os.read(self.process.stdout.fileno(), 65536)
        if not chunk:
            raise HarnessError("language server closed stdout")
        self.buffer += chunk
        if len(self.buffer) > MAX_LSP_MESSAGE_BYTES:
            raise HarnessError("language server response exceeds the size limit")

    def _message(self, deadline: float) -> dict:
        marker = b"\r\n\r\n"
        while marker not in self.buffer:
            self._read_more(deadline)
        raw_headers, remaining = self.buffer.split(marker, 1)
        headers = {}
        for line in raw_headers.decode("ascii", errors="replace").split("\r\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower().strip()] = value.strip()
        try:
            length = int(headers["content-length"])
        except (KeyError, ValueError) as error:
            raise HarnessError("language server response lacks Content-Length") from error
        if length < 0 or length > MAX_LSP_MESSAGE_BYTES:
            raise HarnessError("language server response exceeds the size limit")
        while len(remaining) < length:
            self.buffer = remaining
            self._read_more(deadline)
            remaining = self.buffer
        body, self.buffer = remaining[:length], remaining[length:]
        value = json.loads(body)
        if not isinstance(value, dict):
            raise HarnessError("language server returned a non-object message")
        return value

    def request(self, method: str, params: dict | None = None) -> object:
        self.sequence += 1
        request_id = self.sequence
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = time.monotonic() + self.timeout
        while True:
            response = self._message(deadline)
            if response.get("id") == request_id and "method" not in response:
                if "error" in response:
                    raise HarnessError(f"language server {method} failed: {response['error']}")
                return response.get("result")
            if "id" in response and "method" in response:
                result: object = [] if response["method"] == "workspace/configuration" else None
                self._send({"jsonrpc": "2.0", "id": response["id"], "result": result})

    def initialise(self) -> None:
        self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": self.root.as_uri(),
                "workspaceFolders": [{"uri": self.root.as_uri(), "name": self.root.name}],
                "capabilities": {
                    "workspace": {"configuration": True},
                    "textDocument": {"definition": {}, "references": {}},
                },
            },
        )
        self.notify("initialized", {})

    def close(self) -> None:
        with suppress(Exception):
            self.request("shutdown")
            self.notify("exit")
        with suppress(Exception):
            self.selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            with suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=0.5)
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        with suppress(Exception):
            if self.process.stdin is not None:
                self.process.stdin.close()
        with suppress(Exception):
            if self.process.stdout is not None:
                self.process.stdout.close()


class CodeNavigator:
    def __init__(
        self,
        root: Path,
        *,
        timeout: float = 5.0,
        max_results: int = 40,
        server_commands: dict[str, list[list[str]]] | None = None,
        client_factory: Callable[[list[str], Path, float], LspClient] = LspClient,
    ) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise HarnessError(f"navigation root is not a directory: {self.root}")
        if timeout <= 0 or timeout > 30:
            raise HarnessError("navigation timeout must be between 0 and 30 seconds")
        if max_results < 1 or max_results > 100:
            raise HarnessError("navigation max-results must be between 1 and 100")
        self.timeout = timeout
        self.max_results = max_results
        self.server_commands = server_commands or {}
        self.client_factory = client_factory

    def _file(self, raw: Path) -> Path:
        candidate = raw if raw.is_absolute() else self.root / raw
        candidate = candidate.resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise HarnessError("navigation file is outside the project root")
        if not candidate.is_file():
            raise HarnessError(f"navigation file does not exist: {candidate}")
        return candidate

    @staticmethod
    def _available(command: list[str]) -> bool:
        return bool(command and shutil.which(command[0]))

    def _commands(self, suffix: str) -> list[list[str]]:
        if suffix in self.server_commands:
            return self.server_commands[suffix]
        specification = LANGUAGES.get(suffix)
        return [list(command) for command in specification[1]] if specification else []

    def capabilities(self) -> dict:
        languages = []
        seen = set()
        for suffix, (language, _) in LANGUAGES.items():
            if language in seen:
                continue
            seen.add(language)
            command = next(
                (item for item in self._commands(suffix) if self._available(item)), None
            )
            languages.append(
                {
                    "language": language,
                    "source": "LSP" if command else "TEXT_FALLBACK",
                    "server": command[0] if command else None,
                }
            )
        return {"schema_version": 1, "languages": languages}

    def _symbol(self, path: Path, line: int, column: int, supplied: str | None) -> str:
        if supplied is not None:
            if not IDENTIFIER.fullmatch(supplied):
                raise HarnessError("navigation symbol must be one identifier")
            return supplied
        lines = path.read_text(errors="replace").splitlines()
        if line < 1 or line > len(lines):
            raise HarnessError("navigation line is outside the file")
        text = lines[line - 1]
        index = max(0, column - 1)
        for match in IDENTIFIER.finditer(text):
            if match.start() <= index < match.end() or index == match.end():
                return match.group()
        raise HarnessError("navigation position does not identify a symbol")

    def _locations(self, value: object) -> list[dict]:
        raw_locations = value if isinstance(value, list) else [value]
        locations = []
        for item in raw_locations:
            if not isinstance(item, dict):
                continue
            uri = item.get("uri") or item.get("targetUri")
            span = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange")
            if not isinstance(uri, str) or not isinstance(span, dict):
                continue
            parsed = urlparse(uri)
            if parsed.scheme != "file":
                continue
            path = Path(unquote(parsed.path)).resolve()
            if path != self.root and self.root not in path.parents:
                continue
            start = span.get("start", {})
            end = span.get("end", start)
            if not isinstance(start, dict) or not isinstance(end, dict):
                continue
            locations.append(
                {
                    "path": path.relative_to(self.root).as_posix(),
                    "line": int(start.get("line", 0)) + 1,
                    "column": int(start.get("character", 0)) + 1,
                    "end_line": int(end.get("line", start.get("line", 0))) + 1,
                    "end_column": int(end.get("character", start.get("character", 0))) + 1,
                }
            )
        return self._bounded(locations, limit=self.max_results + 1)

    def _bounded(self, locations: list[dict], *, limit: int | None = None) -> list[dict]:
        unique = []
        seen = set()
        for location in locations:
            key = tuple(location.items())
            if key not in seen:
                seen.add(key)
                unique.append(location)
        return unique[: (limit or self.max_results)]

    @staticmethod
    def _lsp_character(path: Path, line: int, column: int) -> int:
        text = path.read_text(errors="replace").splitlines()[line - 1]
        prefix = text[: column - 1]
        return len(prefix.encode("utf-16-le")) // 2

    def _lsp(
        self,
        operation: str,
        path: Path,
        line: int,
        column: int,
        include_declaration: bool,
    ) -> tuple[list[dict] | None, list[str] | None, str | None]:
        specification = LANGUAGES.get(path.suffix.lower())
        commands = self._commands(path.suffix.lower())
        command = next((item for item in commands if self._available(item)), None)
        if specification is None or command is None:
            return None, None, None
        client = None
        try:
            client = self.client_factory(command, self.root, self.timeout)
            client.initialise()
            uri = path.as_uri()
            client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": specification[0],
                        "version": 1,
                        "text": path.read_text(errors="replace"),
                    }
                },
            )
            params = {
                "textDocument": {"uri": uri},
                "position": {
                    "line": line - 1,
                    "character": self._lsp_character(path, line, column),
                },
            }
            if operation == "references":
                params["context"] = {"includeDeclaration": include_declaration}
            value = client.request(f"textDocument/{operation}", params)
            return self._locations(value), command, None
        except (HarnessError, OSError, TimeoutError, TypeError, ValueError) as error:
            return None, command, str(error)[:240]
        finally:
            if client is not None:
                client.close()

    def _fallback(self, symbol: str, paths: list[Path] | None) -> tuple[list[dict], bool]:
        search_paths = []
        for raw in paths or [Path(".")]:
            candidate = raw if raw.is_absolute() else self.root / raw
            candidate = candidate.resolve()
            if candidate != self.root and self.root not in candidate.parents:
                raise HarnessError("navigation search path is outside the project root")
            if not candidate.exists():
                raise HarnessError(f"navigation search path does not exist: {candidate}")
            search_paths.append(candidate.relative_to(self.root).as_posix() or ".")
        command = [
            "rg", "--json", "--fixed-strings", "--word-regexp",
            "--glob", "!.git/**", "--glob", "!.harness/**", "--", symbol,
            *search_paths,
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise HarnessError("bounded text navigation timed out") from error
        if completed.returncode not in {0, 1}:
            raise HarnessError("bounded text navigation failed: " + completed.stderr.strip())
        locations = []
        total = 0
        for line in completed.stdout.splitlines():
            record = json.loads(line)
            if record.get("type") != "match":
                continue
            data = record["data"]
            relative = data["path"]["text"]
            for match in data.get("submatches", []):
                total += 1
                if len(locations) < self.max_results:
                    locations.append(
                        {
                            "path": relative,
                            "line": data["line_number"],
                            "column": match["start"] + 1,
                            "end_line": data["line_number"],
                            "end_column": match["end"] + 1,
                        }
                    )
        return self._bounded(locations), total > self.max_results

    def query(
        self,
        operation: str,
        file: Path,
        line: int,
        column: int,
        *,
        symbol: str | None = None,
        paths: list[Path] | None = None,
        include_declaration: bool = False,
    ) -> dict:
        if operation not in {"definition", "references"}:
            raise HarnessError("navigation operation must be definition or references")
        if line < 1 or column < 1:
            raise HarnessError("navigation line and column are one-based positive integers")
        path = self._file(file)
        source_lines = path.read_text(errors="replace").splitlines()
        if line > len(source_lines):
            raise HarnessError("navigation line is outside the file")
        if column > len(source_lines[line - 1]) + 1:
            raise HarnessError("navigation column is outside the line")
        name = self._symbol(path, line, column, symbol)
        locations, command, lsp_error = self._lsp(
            operation, path, line, column, include_declaration
        )
        if locations:
            truncated = len(locations) > self.max_results
            return {
                "schema_version": 1,
                "operation": operation,
                "symbol": name,
                "source": "LSP",
                "server": shlex.join(command or []),
                "authoritative": False,
                "coverage": "SERVER_REPORTED",
                "truncated": truncated,
                "locations": locations[: self.max_results],
            }
        fallback, truncated = self._fallback(name, paths)
        result = {
            "schema_version": 1,
            "operation": operation,
            "symbol": name,
            "source": "TEXT_FALLBACK",
            "server": shlex.join(command) if command else None,
            "authoritative": False,
            "coverage": "TEXT_OCCURRENCES_ONLY",
            "truncated": truncated,
            "locations": fallback,
        }
        if lsp_error:
            result["lsp_error"] = lsp_error
        return result
