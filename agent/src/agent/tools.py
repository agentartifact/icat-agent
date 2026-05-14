from __future__ import annotations
import re as _re
import json as _json
import logging
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def normalize_docker_path(path: str, repo_path: str = "/testbed") -> str:
    """Normalize a path to {repo_path}/... format."""
    if path.startswith(repo_path):
        return path
    elif path.startswith("./"):
        return f"{repo_path}/{path[2:]}"
    elif path.startswith("/"):
        return f"{repo_path}{path}"
    else:
        return f"{repo_path}/{path}"


# Builtins set used by trace_call_chain (module-level constant)
_BUILTINS = frozenset({
    'print', 'len', 'str', 'int', 'float', 'list', 'dict', 'set',
    'tuple', 'range', 'enumerate', 'isinstance', 'hasattr', 'getattr',
    'setattr', 'super', 'type', 'bool', 'any', 'all', 'zip', 'map',
    'filter', 'sorted', 'reversed', 'min', 'max', 'sum', 'abs',
    'format', 'repr', 'id', 'hash', 'callable', 'iter', 'next',
    'open', 'property', 'staticmethod', 'classmethod', 'def', 'if',
    'for', 'while', 'with', 'return', 'raise', 'import', 'from',
    'not', 'and', 'or',
})


# Tool factories — each returns a @tool-decorated function

def make_find_files(docker_env):
    """Create a find_files tool bound to the given Docker environment."""
    @tool
    def find_files(pattern: str):
        """Find files by name pattern (wildcards supported)."""
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"
        cmd = f"cd {docker_env.repo_path} && find . -name '{pattern}' -type f -not -path '*/.git/*' 2>/dev/null | head -30"
        returncode, stdout, _ = docker_env.run_command(cmd, timeout=15)
        if returncode == 0 and stdout.strip():
            return "\n".join(f.strip().lstrip('./') for f in stdout.strip().split('\n') if f.strip())
        # Fallback: substring search across all source files
        cmd = f"cd {docker_env.repo_path} && find . -type f -not -path '*/.git/*' -not -path '*/node_modules/*' -not -path '*/__pycache__/*' -not -name '*.pyc' -not -name '*.class' -not -name '*.o' 2>/dev/null | grep -i '{pattern}' | head -30"
        returncode, stdout, _ = docker_env.run_command(cmd, timeout=15)
        if returncode == 0 and stdout.strip():
            return "\n".join(f.strip().lstrip('./') for f in stdout.strip().split('\n') if f.strip())
        return "[none]"
    return find_files


def make_grep_content(docker_env):
    """Create a grep_content tool bound to the given Docker environment."""
    @tool
    def grep_content(pattern: str, path: str = "", file_pattern: str = ""):
        """Search file contents for a pattern. Returns matching lines with line numbers.

        Args:
            pattern: Text or regex to search for
            path: File or directory to search in (default: whole repo)
            file_pattern: Glob to filter files (default: all source files)
        """
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"

        if path:
            full_path = normalize_docker_path(path, docker_env.repo_path)
            # Check if path is a file or directory
            rc, stdout, _ = docker_env.run_command(
                f"test -f {full_path} && echo 'file' || (test -d {full_path} && echo 'dir' || echo 'notfound')"
            )
            path_type = stdout.strip()
            if path_type == "notfound":
                return f"[ERROR] Path not found: {path}"
            if path_type == "file":
                cmd = f"grep -n -C 3 '{pattern}' {full_path} 2>/dev/null | head -100"
            else:
                include = f"--include='{file_pattern}'" if file_pattern else "--include='*.py' --include='*.java' --include='*.js' --include='*.jsx' --include='*.ts' --include='*.tsx' --include='*.go' --include='*.rs' --include='*.c' --include='*.cpp' --include='*.h' --include='*.rb' --include='*.yml' --include='*.yaml' --include='*.json' --include='*.cfg' --include='*.conf' --include='*.txt' --include='*.md' --include='*.sh'"
                cmd = f"grep -rn {include} -C 3 '{pattern}' {full_path} 2>/dev/null | head -100"
        else:
            include = f"--include='{file_pattern}'" if file_pattern else "--include='*.py' --include='*.java' --include='*.js' --include='*.jsx' --include='*.ts' --include='*.tsx' --include='*.go' --include='*.rs' --include='*.c' --include='*.cpp' --include='*.h' --include='*.rb' --include='*.yml' --include='*.yaml' --include='*.json' --include='*.cfg' --include='*.conf' --include='*.txt' --include='*.md' --include='*.sh'"
            cmd = f"cd {docker_env.repo_path} && grep -rn {include} '{pattern}' . 2>/dev/null | head -50"

        returncode, stdout, _ = docker_env.run_command(cmd, timeout=30)
        if returncode != 0 or not stdout.strip():
            target = path or (f"{file_pattern} files" if file_pattern else "source files")
            return f"[No matches found for '{pattern}' in {target}]"
        lines = stdout.strip().split('\n')
        target = path or "repo"
        result = f"[Found {len(lines)} matches for '{pattern}' in {target}]\n"
        result += "\n".join(lines[:100])
        if len(lines) > 100:
            result += f"\n... and {len(lines) - 100} more matches"
        return result
    return grep_content


_MAX_VIEW_OUTPUT_CHARS = 10000

def make_view_file(docker_env, *, max_lines_cap: int = 800, on_view_callback=None, truncate_output: bool = False):
    """Create a view_file tool with line-range support.

    Args:
        docker_env: Docker environment instance
        max_lines_cap: Upper bound on max_lines parameter
        on_view_callback: Optional callable(path) called after a successful file view
    """
    @tool
    def view_file(path: str, start_line: int = 1, max_lines: int = 50):
        """Read file contents with line numbers. Use view_outline or view_symbol for more efficient exploration.

        Args:
            path: File path relative to repo root
            start_line: Starting line number (default 1)
            max_lines: Max lines to return (default 50, max 100). Prefer view_symbol for specific functions.
        """
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"
        requested_max = max_lines
        max_lines = min(max_lines, max_lines_cap)
        if requested_max != max_lines:
            logger.info(f"[view_file] max_lines capped: {requested_max} -> {max_lines}")
        start_line = max(1, start_line)
        full_path = normalize_docker_path(path, docker_env.repo_path)

        returncode, stdout, _ = docker_env.run_command(
            f"test -f {full_path} && echo 'file' || (test -d {full_path} && echo 'dir' || echo 'notfound')"
        )
        check = stdout.strip()
        if check == "notfound":
            return f"[ERROR] File not found: {path}"
        if check == "dir":
            return f"[ERROR] {path} is a directory, not a file. Use list_dir instead."

        end_line = start_line + max_lines - 1
        returncode, stdout, stderr = docker_env.run_command(
            f"sed -n '{start_line},{end_line}p' {full_path}"
        )
        if returncode != 0:
            return f"[ERROR] Cannot read {path}: {stderr}"

        _, count_out, _ = docker_env.run_command(f"wc -l < {full_path}")
        total = int(count_out.strip()) if count_out.strip().isdigit() else 0

        # Add line numbers for easy reference (needed for edit_file)
        raw_lines = stdout.splitlines()
        shown = len(raw_lines)
        numbered = "\n".join(f"{start_line + i:>5} | {line}" for i, line in enumerate(raw_lines))

        result = f"[{path}] (lines {start_line}-{start_line + shown - 1} of {total})\n{numbered}"
        remaining = total - (start_line + shown - 1)
        if remaining > 0:
            result += f"\n... ({remaining} more lines after line {start_line + shown - 1})"

        # Optionally truncate large outputs (10K char limit)
        if truncate_output and len(result) > _MAX_VIEW_OUTPUT_CHARS:
            half = _MAX_VIEW_OUTPUT_CHARS // 2
            result = (
                result[:half]
                + f"\n\n<output clipped — {len(result) - _MAX_VIEW_OUTPUT_CHARS} chars elided>"
                " Use narrower line ranges or view_symbol/view_outline instead.\n\n"
                + result[-half:]
            )

        if on_view_callback:
            on_view_callback(path)

        return result
    return view_file


def make_list_dir(docker_env):
    """Create a list_dir tool (directory listing, replaces get_tree and view_dir)."""
    @tool
    def list_dir(path: str = "."):
        """List directory contents.

        Args:
            path: Directory path relative to repo root (default: root)
        """
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"
        full_path = normalize_docker_path(path, docker_env.repo_path)
        returncode, stdout, stderr = docker_env.run_command(
            f"ls -la {full_path} 2>/dev/null | head -60", timeout=10
        )
        if returncode != 0 or not stdout.strip():
            return f"[ERROR] Directory not found: {path}. Use view_file('{path}') if it's a file."
        return f"[{path}]\n{stdout.strip()}"
    return list_dir


def make_view_symbol(docker_env):
    """Create a view_symbol tool that uses AST to extract complete function/class definitions."""

    _VIEW_SYMBOL_SCRIPT = r'''
import ast, sys, json

filepath = sys.argv[1]
symbol_name = sys.argv[2]

with open(filepath, 'r', errors='replace') as f:
    source = f.read()
source_lines = source.splitlines(True)  # keepends=True

tree = ast.parse(source, filename=filepath)

def get_decorator_start(node):
    """Get the line of the first decorator, or the node itself."""
    if hasattr(node, 'decorator_list') and node.decorator_list:
        return node.decorator_list[0].lineno
    return node.lineno

def get_node_end(node):
    """Get end_lineno, falling back to scanning for dedent."""
    if hasattr(node, 'end_lineno') and node.end_lineno is not None:
        return node.end_lineno
    # Fallback for Python < 3.8: scan for dedent
    indent = len(source_lines[node.lineno - 1]) - len(source_lines[node.lineno - 1].lstrip())
    end = node.lineno
    for i in range(node.lineno, len(source_lines)):
        line = source_lines[i]
        stripped = line.strip()
        if stripped == '' or stripped.startswith('#'):
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= indent and stripped:
            break
        end = i + 1
    return end

# Walk the AST to find all matching symbols
matches = []

def visit_node(node, parent_class=None):
    """Visit AST nodes, tracking parent class for methods."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child.name == symbol_name:
                start = get_decorator_start(child)
                end = get_node_end(child)
                matches.append({
                    'kind': 'async_function' if isinstance(child, ast.AsyncFunctionDef) else 'function',
                    'name': child.name,
                    'start': start,
                    'end': end,
                    'parent_class': parent_class,
                    'args': ast.dump(child.args) if hasattr(ast, 'dump') else '',
                })
            # Also recurse into nested functions
            visit_node(child, parent_class)
        elif isinstance(child, ast.ClassDef):
            if child.name == symbol_name:
                start = get_decorator_start(child)
                end = get_node_end(child)
                matches.append({
                    'kind': 'class',
                    'name': child.name,
                    'start': start,
                    'end': end,
                    'parent_class': parent_class,
                })
            # Recurse into class body to find methods
            visit_node(child, child.name)
        else:
            visit_node(child, parent_class)

visit_node(tree)
print(json.dumps(matches))
'''

    @tool
    def view_symbol(path: str, symbol_name: str) -> str:
        """Extract a complete function or class definition via AST.

        Args:
            path: File path relative to repo root (supports .py, .java, .js)
            symbol_name: Function, method, or class name to extract
        """
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"
        full_path = normalize_docker_path(path, docker_env.repo_path)

        returncode, stdout, _ = docker_env.run_command(
            f"test -f {full_path} && echo 'exists' || echo 'notfound'"
        )
        if stdout.strip() == "notfound":
            return f"[ERROR] File not found: {path}"

        # Check if this is a tree-sitter supported language (Java/JavaScript)
        from agent.treesitter_outline import get_language_for_file, treesitter_find_symbol
        ts_lang = get_language_for_file(path)

        if ts_lang is not None:
            import shlex
            rc, source, stderr = docker_env.run_command(
                f"cat {shlex.quote(full_path)}", timeout=30
            )
            if rc != 0:
                return f"[ERROR] Cannot read {path}: {stderr[:300]}"
            try:
                matches = treesitter_find_symbol(source, ts_lang, symbol_name)
            except Exception as e:
                return f"[ERROR] Parse failed for {path}: {str(e)[:300]}"
        else:
            # Python: use AST
            import shlex
            rc, stdout, stderr = docker_env.run_command(
                f"python3 -c {shlex.quote(_VIEW_SYMBOL_SCRIPT)} {shlex.quote(full_path)} {shlex.quote(symbol_name)}",
                timeout=30
            )
            if rc != 0:
                return f"[ERROR] AST parse failed for {path}: {stderr[:300]}"

            try:
                matches = _json.loads(stdout.strip())
            except _json.JSONDecodeError:
                return f"[ERROR] Failed to parse AST output: {stdout[:300]}"

        if not matches:
            return f"[ERROR] Symbol '{symbol_name}' not found in {path}. Use view_outline to see available symbols."

        results = []
        for match in matches:
            start, end = match['start'], match['end']
            kind = match['kind']
            parent = match.get('parent_class')

            # Read the actual source lines
            rc2, src, _ = docker_env.run_command(
                f"sed -n '{start},{end}p' {full_path}"
            )
            if rc2 != 0 or not src.strip():
                continue

            # Add line numbers
            numbered_lines = []
            for i, line in enumerate(src.splitlines(), start=start):
                numbered_lines.append(f"{i:>5} | {line}")
            numbered_src = "\n".join(numbered_lines)

            header = f"[{path}:{symbol_name}] {kind}"
            if parent:
                header += f" in class {parent}"
            header += f" (lines {start}-{end})"

            results.append(f"{header}\n{numbered_src}")

        if not results:
            return f"[ERROR] Could not extract '{symbol_name}' from {path}"

        return "\n\n".join(results)
    return view_symbol


def make_view_outline(docker_env):
    """Create a view_outline tool that shows file structure using AST."""

    _VIEW_OUTLINE_SCRIPT = r'''
import ast, sys, json

filepath = sys.argv[1]

with open(filepath, 'r', errors='replace') as f:
    source = f.read()

tree = ast.parse(source, filename=filepath)

def get_end(node, source_lines):
    if hasattr(node, 'end_lineno') and node.end_lineno is not None:
        return node.end_lineno
    indent = len(source_lines[node.lineno - 1]) - len(source_lines[node.lineno - 1].lstrip())
    end = node.lineno
    for i in range(node.lineno, len(source_lines)):
        line = source_lines[i]
        stripped = line.strip()
        if stripped == '' or stripped.startswith('#'):
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= indent and stripped:
            break
        end = i + 1
    return end

source_lines = source.splitlines()
outline = []

def format_args(node):
    """Format function arguments concisely."""
    args = []
    for arg in node.args.args:
        name = arg.arg
        if arg.annotation:
            try:
                ann = ast.unparse(arg.annotation)
            except:
                ann = '...'
            args.append(f"{name}: {ann}")
        else:
            args.append(name)
    # Add *args, **kwargs
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    return ', '.join(args)

def format_return(node):
    """Format return annotation."""
    if node.returns:
        try:
            return f" -> {ast.unparse(node.returns)}"
        except:
            return ""
    return ""

for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.ClassDef):
        end = get_end(node, source_lines)
        bases = []
        for base in node.bases:
            try:
                bases.append(ast.unparse(base))
            except:
                bases.append('...')
        base_str = f"({', '.join(bases)})" if bases else ""
        class_entry = {
            'kind': 'class',
            'name': node.name,
            'bases': base_str,
            'start': node.lineno,
            'end': end,
            'methods': [],
        }
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                m_end = get_end(item, source_lines)
                prefix = "async " if isinstance(item, ast.AsyncFunctionDef) else ""
                sig = f"{prefix}def {item.name}({format_args(item)}){format_return(item)}"
                class_entry['methods'].append({
                    'name': item.name,
                    'sig': sig,
                    'start': item.lineno,
                    'end': m_end,
                })
            elif isinstance(item, ast.ClassDef):
                # Nested class
                nc_end = get_end(item, source_lines)
                class_entry['methods'].append({
                    'name': item.name,
                    'sig': f"class {item.name}",
                    'start': item.lineno,
                    'end': nc_end,
                })
        outline.append(class_entry)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        end = get_end(node, source_lines)
        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        sig = f"{prefix}def {node.name}({format_args(node)}){format_return(node)}"
        outline.append({
            'kind': 'function',
            'name': node.name,
            'sig': sig,
            'start': node.lineno,
            'end': end,
        })
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        # Skip imports in outline
        pass
    elif isinstance(node, ast.Assign):
        # Module-level constants
        for target in node.targets:
            try:
                name = ast.unparse(target)
            except:
                name = '...'
            outline.append({
                'kind': 'variable',
                'name': name,
                'start': node.lineno,
                'end': node.lineno,
            })

print(json.dumps(outline))
'''

    @tool
    def view_outline(path: str) -> str:
        """Show structural outline of a source file (classes, methods, functions with line numbers).
        Supports Python (.py), Java (.java), and JavaScript (.js/.jsx/.mjs/.cjs) files.

        Args:
            path: File path relative to repo root
        """
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"
        full_path = normalize_docker_path(path, docker_env.repo_path)

        returncode, stdout, _ = docker_env.run_command(
            f"test -f {full_path} && echo 'exists' || echo 'notfound'"
        )
        if stdout.strip() == "notfound":
            return f"[ERROR] File not found: {path}"

        # Check language support
        from agent.treesitter_outline import get_language_for_file, treesitter_outline, format_outline
        ts_lang = get_language_for_file(path)

        if not path.rstrip('/').endswith('.py') and ts_lang is None:
            return f"[ERROR] view_outline supports Python, Java, and JavaScript files, got: {path}"

        # Use tree-sitter for Java/JavaScript
        if ts_lang is not None:
            import shlex
            rc, source, stderr = docker_env.run_command(
                f"cat {shlex.quote(full_path)}", timeout=30
            )
            if rc != 0:
                return f"[ERROR] Cannot read {path}: {stderr[:300]}"
            try:
                outline = treesitter_outline(source, ts_lang)
            except Exception as e:
                return f"[ERROR] Parse failed for {path}: {str(e)[:300]}"

            if not outline:
                return f"[{path}] (empty or no top-level definitions)"

            _, count_out, _ = docker_env.run_command(f"wc -l < {full_path}")
            total = int(count_out.strip()) if count_out.strip().isdigit() else 0
            return format_outline(outline, path, total)

        # Python: use AST
        import shlex
        rc, stdout, stderr = docker_env.run_command(
            f"python3 -c {shlex.quote(_VIEW_OUTLINE_SCRIPT)} {shlex.quote(full_path)}",
            timeout=30
        )
        if rc != 0:
            return f"[ERROR] AST parse failed for {path}: {stderr[:300]}"

        try:
            outline = _json.loads(stdout.strip())
        except _json.JSONDecodeError:
            return f"[ERROR] Failed to parse outline: {stdout[:300]}"

        if not outline:
            return f"[{path}] (empty or no top-level definitions)"

        # Get total line count
        _, count_out, _ = docker_env.run_command(f"wc -l < {full_path}")
        total = int(count_out.strip()) if count_out.strip().isdigit() else 0

        lines = [f"[{path}] outline ({total} lines total)", ""]
        for entry in outline:
            kind = entry['kind']
            if kind == 'class':
                name = entry['name']
                bases = entry.get('bases', '')
                start, end = entry['start'], entry['end']
                lines.append(f"  class {name}{bases}  (L{start}-{end})")
                for method in entry.get('methods', []):
                    m_start, m_end = method['start'], method['end']
                    lines.append(f"    {method['sig']}  (L{m_start}-{m_end})")
                lines.append("")
            elif kind == 'function':
                start, end = entry['start'], entry['end']
                lines.append(f"  {entry['sig']}  (L{start}-{end})")
            elif kind == 'variable':
                lines.append(f"  {entry['name']} = ...  (L{entry['start']})")

        return "\n".join(lines)
    return view_outline


# ---------------------------------------------------------------------------
# Edit tools — line-range and AST-based editing
# ---------------------------------------------------------------------------

def _write_file_base64(docker_env, full_path, content):
    """Write content to a file in Docker using base64 encoding (avoids ARG_MAX)."""
    import base64 as _b64
    encoded = _b64.b64encode(content.encode('utf-8')).decode('ascii')
    rc, _, stderr = docker_env.run_command_with_stdin(
        f"base64 -d > '{full_path}'", input_data=encoded
    )
    if rc != 0:
        return f"[ERROR] Failed to write file: {stderr}"
    # Verify size
    expected = len(content.encode('utf-8'))
    rc2, out, _ = docker_env.run_command(f"wc -c < '{full_path}'")
    if rc2 == 0 and out.strip().isdigit():
        actual = int(out.strip())
        if actual != expected:
            return f"[WARNING] Write size mismatch: expected {expected}, got {actual} bytes"
    return None  # success


def _syntax_check(docker_env, full_path):
    """Run a syntax check on a source file. Returns error string or None.

    NEVER raises — any internal failure (missing language binding, import error,
    docker hiccup) is swallowed so successful file writes don't get masked as
    failed tool calls. Callers should not depend on this for correctness; it's
    advisory.
    """
    try:
        if full_path.endswith('.py'):
            rc, _, stderr = docker_env.run_command(
                f"python3 -c \"import ast; ast.parse(open('{full_path}').read())\"",
                timeout=10
            )
            if rc != 0:
                return f"[SYNTAX ERROR] {stderr.strip()}"
            return None

        # Tree-sitter path for Java, JS, Go, TS
        try:
            from agent.treesitter_outline import get_language_for_file, treesitter_syntax_check
            ts_lang = get_language_for_file(full_path)
        except Exception:
            ts_lang = None

        if ts_lang:
            import shlex
            rc, source, _ = docker_env.run_command(f"cat {shlex.quote(full_path)}", timeout=10)
            if rc == 0 and source:
                try:
                    result = treesitter_syntax_check(source, ts_lang)
                    if result:
                        return result
                except ModuleNotFoundError as e:
                    logger = logging.getLogger("SyntaxCheck")
                    logger.warning(f"tree-sitter language binding missing for {ts_lang}: {e} — skipping syntax check")
                    return None
                except Exception as e:
                    logger = logging.getLogger("SyntaxCheck")
                    logger.warning(f"tree-sitter syntax check failed ({type(e).__name__}): {e} — skipping")
                    return None
        elif full_path.endswith('.go'):
            rc, _, stderr = docker_env.run_command(
                f"gofmt -e {full_path} > /dev/null 2>&1", timeout=10
            )
            if rc != 0:
                return f"[SYNTAX ERROR] {stderr.strip()}"
    except Exception as e:
        logger = logging.getLogger("SyntaxCheck")
        logger.warning(f"_syntax_check unexpected error ({type(e).__name__}): {e} — skipping")
        return None
    return None


def _extract_top_level_names(source, path=""):
    """Extract top-level and class-level function/class names from source using regex."""
    import re
    # Python: def/class
    # Java/JS/TS: class, function, def, func
    # Go: func
    patterns = [
        r'^[ \t]*(?:def|class)\s+(\w+)',           # Python
        r'^[ \t]*(?:public|private|protected|static|abstract|final|default)?\s*(?:class|interface|enum)\s+(\w+)',  # Java
        r'^[ \t]*(?:export\s+)?(?:default\s+)?(?:class|function|async\s+function)\s+(\w+)',  # JS/TS
        r'^[ \t]*func\s+(\w+)',                     # Go
    ]
    names = set()
    for pat in patterns:
        names.update(re.findall(pat, source, re.MULTILINE))
    return names


def _check_deleted_symbols(old_content, new_content, path):
    """Warn if the edit accidentally removed function/class definitions."""
    old_names = _extract_top_level_names(old_content)
    new_names = _extract_top_level_names(new_content)
    deleted = old_names - new_names
    if deleted:
        names = ', '.join(sorted(deleted))
        return (
            f"[WARNING] This edit removed the following definitions: {names}. "
            f"If this was unintentional, undo this change and retry with a smaller old_text/line range."
        )
    return None


def _compact_diff(old_lines, new_lines, start_line):
    """Generate a compact diff showing changed lines."""
    import difflib
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile='before', tofile='after',
        lineterm='', n=1
    ))
    if not diff:
        return "(no changes)"
    # Limit diff output to avoid flooding context
    if len(diff) > 30:
        return "\n".join(diff[:30]) + f"\n... ({len(diff) - 30} more diff lines)"
    return "\n".join(diff)


def make_edit_file(docker_env):
    """Create an edit_file tool using line-range replacement."""
    @tool
    def edit_file(path: str, start_line: int, end_line: int, new_content: str) -> str:
        """Replace lines start_line to end_line with new_content. Use 0 for end_line to insert without removing.

        Args:
            path: File path relative to repo root
            start_line: First line to replace (1-based, inclusive)
            end_line: Last line to replace (inclusive). 0 = insert before start_line.
            new_content: Replacement content
        """
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"
        full_path = normalize_docker_path(path, docker_env.repo_path)

        # Check file exists
        rc, out, _ = docker_env.run_command(
            f"test -f {full_path} && echo 'exists' || echo 'notfound'"
        )
        if out.strip() == "notfound":
            return f"[ERROR] File not found: {path}"

        # Read entire file
        rc, file_content, stderr = docker_env.run_command(f"cat {full_path}")
        if rc != 0:
            return f"[ERROR] Cannot read {path}: {stderr}"

        lines = file_content.splitlines(True)  # keepends
        total = len(lines)

        # Validate line range
        if start_line < 1:
            return f"[ERROR] start_line must be >= 1, got {start_line}"
        if start_line > total + 1:
            return f"[ERROR] start_line {start_line} exceeds file length ({total} lines). Use start_line={total + 1} to append."

        # Handle insert mode (end_line=0): insert before start_line
        if end_line == 0:
            insert_idx = start_line - 1
            new_lines = new_content.splitlines(True)
            # Ensure last line has newline
            if new_lines and not new_lines[-1].endswith('\n'):
                new_lines[-1] += '\n'
            result_lines = lines[:insert_idx] + new_lines + lines[insert_idx:]
            new_file = "".join(result_lines)

            err = _write_file_base64(docker_env, full_path, new_file)
            if err:
                return err

            syn = _syntax_check(docker_env, full_path)
            msg = f"Inserted {len(new_lines)} lines before line {start_line} in {path} ({total} -> {len(result_lines)} lines)"
            if syn:
                msg += f"\n{syn}"
            return msg

        if end_line < start_line:
            return f"[ERROR] end_line ({end_line}) must be >= start_line ({start_line}), or 0 for insert mode"
        if end_line > total:
            return f"[ERROR] end_line {end_line} exceeds file length ({total} lines)"

        # Build new file
        old_section = lines[start_line - 1:end_line]
        new_content_lines = new_content.splitlines(True) if new_content else []
        # Ensure trailing newline
        if new_content_lines and not new_content_lines[-1].endswith('\n'):
            new_content_lines[-1] += '\n'

        result_lines = lines[:start_line - 1] + new_content_lines + lines[end_line:]
        new_file = "".join(result_lines)

        # Safety check: detect accidentally deleted function/class definitions
        warnings = _check_deleted_symbols(file_content, new_file, path)

        # Write
        err = _write_file_base64(docker_env, full_path, new_file)
        if err:
            return err

        # Diff
        diff = _compact_diff(
            [l.rstrip('\n') for l in old_section],
            [l.rstrip('\n') for l in new_content_lines],
            start_line
        )

        # Syntax check
        syn = _syntax_check(docker_env, full_path)

        replaced = end_line - start_line + 1
        msg = f"Replaced lines {start_line}-{end_line} ({replaced} lines) with {len(new_content_lines)} lines in {path} ({total} -> {len(result_lines)} lines)"
        msg += f"\n{diff}"
        if warnings:
            msg += f"\n{warnings}"
        if syn:
            msg += f"\n{syn}"
        return msg
    return edit_file


def make_search_replace(docker_env):
    """Create a search_replace tool that finds exact text and replaces it."""
    @tool
    def search_replace(path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        """Find exact text in a file and replace it. Safer than line-range editing — only changes what you specify.

        Args:
            path: File path relative to repo root
            old_text: Exact text to find (must match exactly, including indentation)
            new_text: Text to replace it with
            replace_all: If True, replace ALL occurrences at once (useful for renaming variables/fields across a file)
        """
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"
        full_path = normalize_docker_path(path, docker_env.repo_path)

        rc, file_content, stderr = docker_env.run_command(f"cat {full_path}")
        if rc != 0:
            return f"[ERROR] Cannot read {path}: {stderr}"

        # Check that old_text exists
        count = file_content.count(old_text)
        if count == 0:
            return (
                f"[ERROR] old_text not found in {path}. "
                f"Make sure it matches EXACTLY (including indentation and whitespace). "
                f"Use view_file or view_symbol to see the exact content first."
            )
        if count > 1 and not replace_all:
            return (
                f"[ERROR] old_text found {count} times in {path}. "
                f"Include more surrounding context to make it unique, "
                f"or set replace_all=True to replace all {count} occurrences at once."
            )

        # Replace
        if replace_all:
            new_content = file_content.replace(old_text, new_text)
        else:
            new_content = file_content.replace(old_text, new_text, 1)

        # Safety check: detect accidentally deleted function/class definitions
        warnings = _check_deleted_symbols(file_content, new_content, path)

        err = _write_file_base64(docker_env, full_path, new_content)
        if err:
            return err

        syn = _syntax_check(docker_env, full_path)

        # Show diff
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        diff = _compact_diff(old_lines, new_lines, 0)

        replaced_msg = f"all {count} occurrences" if replace_all and count > 1 else "text"
        msg = f"Successfully replaced {replaced_msg} in {path}"
        if diff and diff != "(no changes)":
            msg += f"\n{diff}"
        if warnings:
            msg += f"\n{warnings}"
        if syn:
            msg += f"\n{syn}"
        return msg
    return search_replace


def make_edit_symbol(docker_env):
    """Create an edit_symbol tool that replaces a function/class body using AST to find boundaries."""

    # Reuse the same AST script from view_symbol to find symbol boundaries
    _FIND_SYMBOL_SCRIPT = r'''
import ast, sys, json

filepath = sys.argv[1]
symbol_name = sys.argv[2]

with open(filepath, 'r', errors='replace') as f:
    source = f.read()
source_lines = source.splitlines(True)

tree = ast.parse(source, filename=filepath)

def get_decorator_start(node):
    if hasattr(node, 'decorator_list') and node.decorator_list:
        return node.decorator_list[0].lineno
    return node.lineno

def get_end(node):
    if hasattr(node, 'end_lineno') and node.end_lineno is not None:
        return node.end_lineno
    indent = len(source_lines[node.lineno - 1]) - len(source_lines[node.lineno - 1].lstrip())
    end = node.lineno
    for i in range(node.lineno, len(source_lines)):
        line = source_lines[i]
        stripped = line.strip()
        if stripped == '' or stripped.startswith('#'):
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= indent and stripped:
            break
        end = i + 1
    return end

matches = []
def visit(node, parent_class=None):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child.name == symbol_name:
                matches.append({
                    'kind': 'function',
                    'start': get_decorator_start(child),
                    'end': get_end(child),
                    'parent': parent_class,
                })
            visit(child, parent_class)
        elif isinstance(child, ast.ClassDef):
            if child.name == symbol_name:
                matches.append({
                    'kind': 'class',
                    'start': get_decorator_start(child),
                    'end': get_end(child),
                    'parent': parent_class,
                })
            visit(child, child.name)
        else:
            visit(child, parent_class)

visit(tree)
print(json.dumps(matches))
'''

    @tool
    def edit_symbol(path: str, symbol_name: str, new_body: str, class_name: str = "") -> str:
        """Replace an entire function/method/class definition found via AST.

        Args:
            path: File path relative to repo root
            symbol_name: Function, method, or class name to replace
            new_body: Complete new definition (including def/class line and full body)
            class_name: Class name to disambiguate methods (optional)
        """
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"
        full_path = normalize_docker_path(path, docker_env.repo_path)

        rc, out, _ = docker_env.run_command(
            f"test -f {full_path} && echo 'exists' || echo 'notfound'"
        )
        if out.strip() == "notfound":
            return f"[ERROR] File not found: {path}"

        # Find symbol boundaries — use tree-sitter for Java/JS, AST for Python
        import shlex
        from agent.treesitter_outline import get_language_for_file, treesitter_find_symbol
        ts_lang = get_language_for_file(path)

        if ts_lang is not None:
            rc, source, stderr = docker_env.run_command(
                f"cat {shlex.quote(full_path)}", timeout=30
            )
            if rc != 0:
                return f"[ERROR] Cannot read {path}: {stderr[:300]}"
            try:
                matches = treesitter_find_symbol(source, ts_lang, symbol_name)
            except Exception as e:
                return f"[ERROR] Parse failed for {path}: {str(e)[:300]}"
        else:
            rc, stdout, stderr = docker_env.run_command(
                f"python3 -c {shlex.quote(_FIND_SYMBOL_SCRIPT)} {shlex.quote(full_path)} {shlex.quote(symbol_name)}",
                timeout=30
            )
            if rc != 0:
                return f"[ERROR] AST parse failed for {path}: {stderr[:300]}"

            try:
                matches = _json.loads(stdout.strip())
            except _json.JSONDecodeError:
                return f"[ERROR] Failed to parse AST output: {stdout[:300]}"

        if not matches:
            return f"[ERROR] Symbol '{symbol_name}' not found in {path}. Use view_outline to see available symbols."

        # Disambiguate by class_name if provided
        if class_name:
            filtered = [m for m in matches if m.get('parent') == class_name]
            if filtered:
                matches = filtered
            else:
                parents = [m.get('parent', 'module-level') for m in matches]
                return f"[ERROR] '{symbol_name}' not found in class '{class_name}'. Found in: {', '.join(set(str(p) for p in parents))}"

        if len(matches) > 1 and not class_name:
            locs = [f"L{m['start']}-{m['end']} in {m.get('parent') or 'module'}" for m in matches]
            return f"[ERROR] Multiple definitions of '{symbol_name}' found: {'; '.join(locs)}. Use class_name to disambiguate."

        match = matches[0]
        start_line = match['start']
        end_line = match['end']

        # Read file
        rc, file_content, stderr = docker_env.run_command(f"cat {full_path}")
        if rc != 0:
            return f"[ERROR] Cannot read {path}: {stderr}"

        lines = file_content.splitlines(True)
        total = len(lines)

        old_section = lines[start_line - 1:end_line]
        new_lines = new_body.splitlines(True)
        # Ensure trailing newline
        if new_lines and not new_lines[-1].endswith('\n'):
            new_lines[-1] += '\n'

        result_lines = lines[:start_line - 1] + new_lines + lines[end_line:]
        new_file = "".join(result_lines)

        # Write
        err = _write_file_base64(docker_env, full_path, new_file)
        if err:
            return err

        # Diff
        diff = _compact_diff(
            [l.rstrip('\n') for l in old_section],
            [l.rstrip('\n') for l in new_lines],
            start_line
        )

        # Syntax check
        syn = _syntax_check(docker_env, full_path)

        kind = match['kind']
        parent = match.get('parent')
        loc = f"{kind} '{symbol_name}'"
        if parent:
            loc += f" in class '{parent}'"

        msg = f"Replaced {loc} (lines {start_line}-{end_line}) with {len(new_lines)} lines in {path} ({total} -> {len(result_lines)} lines)"
        msg += f"\n{diff}"
        if syn:
            msg += f"\n{syn}"
        return msg
    return edit_symbol


# make_search_in_file removed — merged into make_grep_content (use path parameter)


def make_trace_call_chain(docker_env):
    """Create a trace_call_chain tool using AST parsing inside Docker for speed."""

    _cache = {}  # Cache across calls within this agent's lifetime

    # AST-based tracing script — runs entirely inside Docker in one command.
    # Parses all .py files once, builds an index, then traces recursively.
    _TRACE_SCRIPT = r'''
import ast, os, json, sys
from collections import defaultdict

MAX_DEPTH = 5
MAX_FUNCS = 50

# --- Phase 1: Build index by parsing all .py files under /testbed ---
# func_defs: {func_name: [(file, lineno), ...]}
# func_callees: {(func_name, file): [callee_name, ...]}
# func_callers: {callee_name: [(caller_func, caller_file), ...]}
# func_siblings: {func_name: set of sibling func names} (e.g. property co-refs)
func_defs = defaultdict(list)
func_callees = {}
func_callers = defaultdict(set)
func_siblings = defaultdict(set)

class CallVisitor(ast.NodeVisitor):
    """Extract function/method call names from a function body."""
    def __init__(self):
        self.calls = set()
    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            self.calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.calls.add(node.func.attr)
        # Capture function names passed as arguments (e.g. property(_get_pk_val, _set_pk_val))
        for arg in node.args:
            if isinstance(arg, ast.Name):
                self.calls.add(arg.id)
        for kw in node.keywords:
            if isinstance(kw.value, ast.Name):
                self.calls.add(kw.value.id)
        self.generic_visit(node)

def index_file(filepath, rel_path):
    try:
        with open(filepath, 'r', errors='replace') as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except:
        return

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fname = node.name
            func_defs[fname].append((rel_path, node.lineno))
            # Extract callees from function body
            cv = CallVisitor()
            cv.visit(node)
            callees = sorted(cv.calls - {fname})
            func_callees[(fname, rel_path)] = callees
            # Register reverse mapping (caller -> callee)
            for callee in callees:
                func_callers[callee].add((fname, rel_path))
        # Handle class-level property/descriptor assignments like:
        #   pk = property(_get_pk_val, _set_pk_val)
        # 1) Register class as caller of each referenced function
        # 2) Link all functions in the same call as co-references (siblings)
        #    so tracing _get_pk_val also surfaces _set_pk_val
        elif isinstance(node, ast.ClassDef):
            class_name = node.name
            for item in node.body:
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Call):
                    # Collect all function names referenced as args
                    co_refs = []
                    for arg in item.value.args:
                        if isinstance(arg, ast.Name):
                            func_callers[arg.id].add((class_name, rel_path))
                            co_refs.append(arg.id)
                    for kw in item.value.keywords:
                        if isinstance(kw.value, ast.Name):
                            func_callers[kw.value.id].add((class_name, rel_path))
                            co_refs.append(kw.value.id)
                    # Link co-referenced functions as siblings
                    # e.g. property(_get_pk_val, _set_pk_val) links them together
                    if len(co_refs) > 1:
                        for fn in co_refs:
                            func_siblings[fn].update(c for c in co_refs if c != fn)

# Walk repo root, skip test dirs and hidden dirs
_REPO_ROOT = os.environ.get('REPO_PATH', '/testbed')
for root, dirs, files in os.walk(_REPO_ROOT):
    # Skip test directories, .git, __pycache__, .tox, node_modules
    dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', '.tox', 'node_modules', '.eggs')
               and 'test' not in d.lower()]
    for fname in files:
        if fname.endswith(('.py', '.java', '.js', '.ts', '.go', '.rs')) and 'test' not in fname.lower():
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, _REPO_ROOT)
            index_file(full, rel)

# --- Phase 2: Recursive tracing ---
visited = set()
graph = {}

def trace(name, hint='', depth=0):
    if name in visited or depth > MAX_DEPTH or len(visited) >= MAX_FUNCS:
        return
    # Skip names that have no definition in the codebase (builtins, exceptions, etc.)
    if depth > 0 and name not in func_defs:
        return
    visited.add(name)

    # Find definitions
    defs = func_defs.get(name, [])
    if hint:
        # Prefer the hinted file
        matching = [d for d in defs if d[0] == hint]
        primary_file = matching[0][0] if matching else (defs[0][0] if defs else None)
    else:
        primary_file = defs[0][0] if defs else None

    def_strs = [f"{f}:{ln}" for f, ln in defs]

    # Get callees from primary definition (cap at 50)
    callees = []
    if primary_file:
        callees = func_callees.get((name, primary_file), [])
        # Also check other files where this function is defined
        for f, _ in defs:
            if f != primary_file:
                callees = list(set(callees + func_callees.get((name, f), [])))
        callees.sort()

    # Get callers (cap at 50)
    callers = sorted(func_callers.get(name, set()))
    callers = callers[:50]

    # Filter callees to only include functions defined in the codebase (cap at 50)
    callees = [c for c in callees if c in func_defs][:50]

    # Get siblings (e.g. _set_pk_val for _get_pk_val via property())
    siblings = sorted(func_siblings.get(name, set()))[:10]

    graph[name] = {
        'file': primary_file,
        'defs': def_strs,
        'callees': callees,
        'callers': [list(c) for c in callers],
        'siblings': siblings,
    }

    # Priority order: siblings first, then same-file, then other files
    # Limit recursion: at most 10 callees and 10 callers traced per function
    _MAX_TRACE_PER_LEVEL = 10
    # Siblings are highest priority (e.g. property getter/setter pairs)
    for s in siblings[:5]:
        if s not in visited and len(visited) < MAX_FUNCS:
            trace(s, hint=primary_file or '', depth=depth + 1)
    same_file_callees = [c for c in callees if c not in visited and primary_file and any(f == primary_file for f, _ in func_defs.get(c, []))]
    other_callees = [c for c in callees if c not in visited and c not in same_file_callees]
    for c in (same_file_callees + other_callees)[:_MAX_TRACE_PER_LEVEL]:
        if len(visited) < MAX_FUNCS:
            trace(c, depth=depth + 1)
    same_file_callers = [(cn, cf) for cn, cf in callers if cn not in visited and cf == primary_file]
    other_callers = [(cn, cf) for cn, cf in callers if cn not in visited and (cn, cf) not in same_file_callers]
    for cn, cf in (same_file_callers + other_callers)[:_MAX_TRACE_PER_LEVEL]:
        if len(visited) < MAX_FUNCS:
            trace(cn, hint=cf, depth=depth + 1)

func_name = sys.argv[1]
hint_file = sys.argv[2] if len(sys.argv) > 2 else ''
trace(func_name, hint_file)
print(json.dumps(graph))
'''

    @tool
    def trace_call_chain(function_name: str, file_path: str = "") -> str:
        """Trace call chain for a function (callees and callers, up to depth 5, max 50 functions, max 10 traced per level). Results are cached.

        Args:
            function_name: Function name to trace
            file_path: Optional file path to narrow search
        """
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"

        cache_key = f"{function_name}:{file_path}"
        if cache_key in _cache:
            return f"[CACHED] {_cache[cache_key]}"

        import json as _json, shlex
        hint_arg = f" {shlex.quote(file_path)}" if file_path else ""
        rc, stdout, stderr = docker_env.run_command(
            f"REPO_PATH={docker_env.repo_path} python3 -c {shlex.quote(_TRACE_SCRIPT)} {shlex.quote(function_name)}{hint_arg}",
            timeout=120
        )

        if rc != 0 or not stdout.strip():
            return f"[ERROR] trace_call_chain failed: {stderr[:500] if stderr else 'no output'}"

        try:
            graph = _json.loads(stdout.strip())
        except _json.JSONDecodeError:
            return f"[ERROR] Failed to parse trace output: {stdout[:500]}"

        # Format output — cap callers/callees to avoid huge context
        _MAX_DISPLAY = 50  # max callers/callees shown per function
        results = [f"=== CALL CHAIN: {function_name} (depth=5, max=50) ==="]
        results.append(f"Total functions traced: {len(graph)}")
        results.append("")

        ordered = [function_name] + sorted(k for k in graph if k != function_name)
        for func in ordered:
            info = graph.get(func, {})
            parts = [func]
            callees = info.get("callees", [])
            if callees:
                shown = callees[:_MAX_DISPLAY]
                suffix = f" (+{len(callees)-_MAX_DISPLAY} more)" if len(callees) > _MAX_DISPLAY else ""
                parts.append(f"callees: {', '.join(shown)}{suffix}")
            callers = info.get("callers", [])
            if callers:
                caller_strs = sorted(set(c[0] for c in callers))[:_MAX_DISPLAY]
                suffix = f" (+{len(callers)-_MAX_DISPLAY} more)" if len(callers) > _MAX_DISPLAY else ""
                parts.append(f"callers: {', '.join(caller_strs)}{suffix}")
            siblings = info.get("siblings", [])
            if siblings:
                parts.append(f"siblings: {', '.join(siblings[:10])}")
            results.append(" | ".join(parts))

        output = "\n".join(results)
        _cache[cache_key] = output
        return output
    return trace_call_chain


def make_share_findings(message_bus, *, agent_name: str, post_callback=None,
                        wait_for_validation: bool = False, docker_env=None):
    """Create a share_findings tool for posting to the message bus.

    Args:
        message_bus: AgentMessageBus instance
        agent_name: Name of the agent posting findings
        post_callback: Optional callable(finding_type, data) for extra side-effects after posting
        wait_for_validation: If True, block after patch_generated until reproducer validates
        docker_env: Docker environment (needed to collect diff before blocking)
    """
    _logger = logging.getLogger(f"share_findings.{agent_name}")

    def _collect_and_post_diff():
        """Collect git diff from Docker and post as a separate message so reproducer can apply it."""
        if not docker_env:
            return
        try:
            repo_path = docker_env.repo_path
            rc, stdout, stderr = docker_env.run_command(
                f"cd {repo_path} && git diff", timeout=15)
            diff = (stdout or "").strip()
            if diff and (diff.startswith("diff ") or "--- a/" in diff):
                message_bus.post(agent_name, "patch_generated", diff)
                _logger.info(f"[{agent_name}] Collected and posted git diff ({len(diff)} chars)")
        except Exception as e:
            _logger.warning(f"[{agent_name}] Failed to collect diff: {e}")

    @tool
    def share_findings(finding_type: str, data: str) -> str:
        """Share findings with other agents.

        Args:
            finding_type: e.g. localized_files, bug_confirmed, patch_generated
            data: What you found
        """
        if message_bus:
            message_bus.post(agent_name, finding_type, data)
            if post_callback:
                post_callback(finding_type, data)

            if wait_for_validation and finding_type == "patch_generated":
                _collect_and_post_diff()
                _logger.info(f"[{agent_name}] Patch shared. Waiting for reproducer validation...")
                feedback = message_bus.wait_for_validation(timeout=600)
                if feedback is None:
                    return ("[Shared with other agents] patch_generated.\n"
                            "WARNING: Timed out waiting for validation (600s). "
                            "The reproducer may not be running. Proceed with caution.")
                fb_type = feedback["type"]
                fb_data = str(feedback["data"])
                if fb_type == "validation_passed":
                    return (f"[Shared with other agents] patch_generated.\n"
                            f"VALIDATION PASSED: {fb_data[:500]}\n"
                            f"Your patch is correct! Declare DONE.")
                else:
                    return (f"[Shared with other agents] patch_generated.\n"
                            f"VALIDATION FAILED: {fb_data[:1000]}\n"
                            f"Please revise your patch based on the feedback above, "
                            f"then call share_findings('patch_generated', ...) again.")

            return f"[Shared with other agents] {finding_type}: {data[:200]}"
        return "[No message bus available - findings not shared]"
    return share_findings


def make_check_findings(message_bus, *, agent_name: str):
    """Create a check_findings tool for reading from the message bus."""
    @tool
    def check_findings() -> str:
        """Check what other agents have found so far.

        Call this periodically to see if other agents have found useful information,
        such as test files, reproduction results, or files being modified.
        """
        if message_bus:
            msgs = message_bus.read_formatted(exclude_from=agent_name)
            if msgs:
                return f"[Messages from other agents]\n{msgs}"
            return "[No messages from other agents yet]"
        return "[No message bus available]"
    return check_findings


def make_query_agents(shared_context, *, agent_name: str):
    """Create a query_agents tool for reading other agents' shared context."""
    @tool
    def query_agents(agent_name_to_query: str = "all"):
        """Query other agents' findings.

        Args:
            agent_name_to_query: "localizer", "reproducer", "patch_editor", or "all"
        """
        context_parts = []

        if agent_name_to_query in ("all", "localizer") and agent_name != "localizer":
            loc_ctx = shared_context.get_localizer_context()
            if loc_ctx.get("status") != "not_started":
                context_parts.append(f"=== Localizer Agent ({loc_ctx.get('status', 'unknown')}) ===")
                if loc_ctx.get("top_candidates"):
                    for c in loc_ctx["top_candidates"][:5]:
                        context_parts.append(f"  Candidate: {c.get('file', '?')}:{c.get('function', '?')} (conf: {c.get('confidence', 0)})")
                if loc_ctx.get("findings"):
                    context_parts.append(f"Findings: {loc_ctx['findings'][:500]}")
            else:
                context_parts.append("=== Localizer Agent: Not started yet ===")

        if agent_name_to_query in ("all", "reproducer") and agent_name != "reproducer":
            rep_ctx = shared_context.get_reproducer_context()
            if rep_ctx.get("status") != "not_started":
                context_parts.append(f"=== Reproducer Agent ({rep_ctx.get('status', 'unknown')}) ===")
                if rep_ctx.get("reproduction_result"):
                    context_parts.append(f"Reproduction result: {rep_ctx['reproduction_result']}")
                if rep_ctx.get("reproduction_script"):
                    context_parts.append(f"Script: {rep_ctx['reproduction_script'][:500]}...")
                if rep_ctx.get("expected_behavior"):
                    context_parts.append(f"Expected: {rep_ctx['expected_behavior']}")
                if rep_ctx.get("actual_behavior"):
                    context_parts.append(f"Actual: {rep_ctx['actual_behavior']}")
            else:
                context_parts.append("=== Reproducer Agent: Not started yet ===")

        if agent_name_to_query in ("all", "patch_editor") and agent_name != "patch_editor":
            pe_ctx = shared_context.get_patch_editor_context()
            if pe_ctx.get("status") != "not_started":
                context_parts.append(f"=== Patch Editor Agent ({pe_ctx.get('status', 'unknown')}) ===")
                if pe_ctx.get("modified_files"):
                    files = [m.get("file", "?") for m in pe_ctx["modified_files"]]
                    context_parts.append(f"Modified files: {', '.join(files)}")
                if pe_ctx.get("attempts"):
                    context_parts.append(f"Edit attempts: {len(pe_ctx['attempts'])}")
            else:
                context_parts.append("=== Patch Editor Agent: Not started yet ===")

        # Show communication messages
        messages = shared_context.get_messages_for(agent_name)
        if messages:
            context_parts.append(f"\n=== Messages for {agent_name.title()} ({len(messages)}) ===")
            for msg in messages[-5:]:
                context_parts.append(f"  [{msg.get('from', '?')}]: {msg.get('message', '')[:200]}")

        return "\n".join(context_parts) if context_parts else "No context available from other agents yet."
    return query_agents


# ---------------------------------------------------------------------------
# Reproducer-specific tools
# ---------------------------------------------------------------------------

def make_run_python(docker_env, *, trajectory_callback=None, context_callback=None):
    """Create a run_python tool that executes Python code in the container.

    Args:
        docker_env: Docker environment instance
        trajectory_callback: Optional callable(action, action_type, input_data, output_data, metadata)
        context_callback: Optional callable(role, action, result, metadata)
    """
    @tool
    def run_python(code: str) -> str:
        """Execute Python code in the repo. Exit 1 = bug reproduced, exit 0 = no bug."""
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"

        tmp_script = "/tmp/_repro_run.py"
        if not docker_env.write_file(tmp_script, code):
            return "[ERROR] Failed to write script to container"
        cmd = f"cd {docker_env.repo_path} && python3 {tmp_script} 2>&1"
        returncode, stdout, stderr = docker_env.run_command(cmd, timeout=60)

        # Truncate long output to avoid context bloat
        max_chars = 5000
        if stdout and len(stdout) > max_chars:
            # Keep first and last portions for context
            head = stdout[:2000]
            tail = stdout[-2000:]
            stdout = f"{head}\n... [truncated, {len(stdout)} chars total] ...\n{tail}"
        if stderr and len(stderr) > max_chars:
            stderr = stderr[:max_chars] + f"\n... [truncated, {len(stderr)} chars total]"

        result = f"Exit code: {returncode}\n"
        if stdout:
            result += f"Output:\n{stdout}\n"
        if stderr:
            result += f"Stderr:\n{stderr}\n"

        if trajectory_callback:
            trajectory_callback(
                "run_python", "tool_call",
                {"code": code}, result,
                {"exit_code": returncode, "command": cmd}
            )
        if context_callback:
            context_callback("reproducer", "run_python", result, {"exit_code": returncode})

        return result
    return run_python


def make_run_regression_tests(
    docker_env, *, instance_id: str, available_tests: list,
    detect_framework_fn=None, test_commands: dict = None,
    convert_test_name_fn=None,
    trajectory_callback=None, context_callback=None,
    message_bus=None,
):
    """Create a run_regression_tests tool.

    Args:
        docker_env: Docker environment instance
        instance_id: SWE-bench instance ID
        available_tests: List of test names to run
        detect_framework_fn: Optional framework detection function
        test_commands: Dict mapping framework names to test commands
        convert_test_name_fn: Optional function to convert test names per framework
        trajectory_callback: Optional callable(action, action_type, input_data, output_data, metadata)
        context_callback: Optional callable(test, result, output) for regression test results
        message_bus: Optional AgentMessageBus for posting results
    """
    _default_commands = {
        "pytest": "python -m pytest -xvs",
        "django": "./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1",
        "sympy": "./bin/test -C --verbose",
        "sphinx": "tox --current-env -epy39 -v --",
        # Non-Python frameworks
        "jest": "npx jest --verbose",
        "mocha": "npx mocha --reporter spec",
        "npm": "npm test --",
        "maven": "mvn test -pl . -Dtest=",
        "gradle": "./gradlew test --tests",
        "go": "go test -v -run",
        "cargo": "cargo test",
    }
    if test_commands is None:
        test_commands = _default_commands

    _call_count = [0]  # mutable counter to track phase
    _registered_tests = []  # tests registered by agent via register_regression_tests

    @tool
    def register_regression_tests(test_commands: list) -> str:
        """Register test commands you discovered so they will be run in future run_regression_tests calls.

        Use this after you identify related test files/functions in the codebase.
        Each entry should be a FULL shell command that can be run directly.
        Examples:
          register_regression_tests(["pytest tests/test_utils.py -xvs", "pytest tests/test_core.py -xvs"])
          register_regression_tests(["go test -v ./core/agents/...", "go test -v -run TestParse ./pkg/parser/"])
          register_regression_tests(["npx jest src/components/__tests__/Button.test.tsx"])

        Args:
            test_commands: List of full shell commands to run as regression tests
        """
        added = 0
        for t in test_commands:
            t = t.strip()
            if t and t not in _registered_tests:
                _registered_tests.append(t)
                added += 1
        return f"[OK] Registered {added} test command(s). Total registered: {len(_registered_tests)}. These will be run in future run_regression_tests calls."

    @tool
    def run_regression_tests() -> str:
        """Run all regression tests for this instance (including any you registered). Auto-detects framework."""
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"

        # Combine pre-filtered + registered tests
        all_prefiltered = list(available_tests)
        all_registered = list(_registered_tests)
        if not all_prefiltered and not all_registered:
            return "[INFO] No pre-filtered regression tests available. You should identify and run related tests yourself — search the codebase for test files in the affected module/package, then call register_regression_tests() to save them for future runs."

        # Detect framework
        if detect_framework_fn:
            framework = detect_framework_fn(instance_id)
            base_cmd = test_commands.get(framework, test_commands.get("pytest", "python -m pytest -xvs"))
            logger.info(f"Detected framework: {framework}, using: {base_cmd}")
        else:
            instance_lower = instance_id.lower()
            if instance_lower.startswith("django"):
                base_cmd = "./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1"
                framework = "django"
            elif instance_lower.startswith("sympy"):
                base_cmd = "./bin/test -C --verbose"
                framework = "sympy"
            elif instance_lower.startswith("sphinx"):
                base_cmd = "tox --current-env -epy39 -v --"
                framework = "sphinx"
            else:
                # Auto-detect non-Python frameworks
                rc_pkg, pkg_out, _ = docker_env.run_command(
                    f"test -f {docker_env.repo_path}/package.json && echo 'node' || echo 'no'", timeout=5)
                rc_pom, pom_out, _ = docker_env.run_command(
                    f"test -f {docker_env.repo_path}/pom.xml && echo 'maven' || echo 'no'", timeout=5)
                rc_gradle, gradle_out, _ = docker_env.run_command(
                    f"test -f {docker_env.repo_path}/build.gradle && echo 'gradle' || "
                    f"test -f {docker_env.repo_path}/build.gradle.kts && echo 'gradle' || echo 'no'", timeout=5)
                rc_go, go_out, _ = docker_env.run_command(
                    f"test -f {docker_env.repo_path}/go.mod && echo 'go' || echo 'no'", timeout=5)
                rc_cargo, cargo_out, _ = docker_env.run_command(
                    f"test -f {docker_env.repo_path}/Cargo.toml && echo 'cargo' || echo 'no'", timeout=5)

                if 'node' in pkg_out:
                    # Check for jest or mocha
                    rc_jest, jest_out, _ = docker_env.run_command(
                        f"grep -q 'jest' {docker_env.repo_path}/package.json && echo 'jest' || echo 'no'", timeout=5)
                    if 'jest' in jest_out:
                        base_cmd = "npx jest --verbose"
                        framework = "jest"
                    else:
                        base_cmd = "npm test --"
                        framework = "npm"
                elif 'maven' in pom_out:
                    base_cmd = "mvn test -pl . -Dtest="
                    framework = "maven"
                elif 'gradle' in gradle_out:
                    base_cmd = "./gradlew test --tests"
                    framework = "gradle"
                elif 'go' in go_out:
                    base_cmd = "go test -v -run"
                    framework = "go"
                elif 'cargo' in cargo_out:
                    base_cmd = "cargo test"
                    framework = "cargo"
                else:
                    base_cmd = "python -m pytest -xvs"
                    framework = "pytest"
            logger.info(f"Using fallback framework detection: {framework}")

        results = []
        passed_count = 0
        failed_count = 0
        all_tests = all_prefiltered + all_registered

        logger.info(f"Running {len(all_tests)} regression tests ({len(all_prefiltered)} pre-filtered + {len(all_registered)} registered)...")

        # Build commands: pre-filtered use framework detection, registered run directly
        all_commands = []
        for test_name in all_prefiltered:
            converted_name = test_name
            if convert_test_name_fn is not None:
                converted_name = convert_test_name_fn(test_name, framework)
            all_commands.append((test_name, f"cd {docker_env.repo_path} && {base_cmd} {converted_name} 2>&1"))
        for test_cmd in all_registered:
            all_commands.append((test_cmd, f"cd {docker_env.repo_path} && {test_cmd} 2>&1"))

        # Run tests individually to get per-test results
        for test_name, cmd in all_commands:
            returncode, stdout, stderr = docker_env.run_command(cmd, timeout=300)

            status = "PASSED" if returncode == 0 else "FAILED"
            if returncode == 0:
                passed_count += 1
            else:
                failed_count += 1

            test_result = {
                "test": test_name,
                "status": status,
                "exit_code": returncode,
                "output": stdout if stdout else "",
                "stderr": stderr if stderr else ""
            }
            results.append(test_result)

        # Single trajectory callback with aggregated results
        if trajectory_callback:
            trajectory_callback(
                "run_regression_tests", "regression_test",
                {"framework": framework, "test_count": len(all_tests)},
                f"{passed_count}P/{failed_count}F of {len(all_tests)} tests",
                {"passed": passed_count, "failed": failed_count}
            )

        # Single context callback with summary only
        if context_callback:
            context_callback(
                "regression_tests",
                "passed" if failed_count == 0 else "failed",
                f"{passed_count} passed, {failed_count} failed of {len(available_tests)} tests"
            )

        # Determine phase based on call count
        phase = "before" if _call_count[0] == 0 else "after"
        _call_count[0] += 1

        # Build compact summary — only show details for failures
        summary = f"""=== REGRESSION TEST RESULTS ({phase} patch) ===
Framework: {framework} | Total: {len(all_tests)} | Passed: {passed_count} | Failed: {failed_count}
"""
        if failed_count == 0:
            summary += "All tests passed.\n"
        else:
            for r in results:
                if r['status'] == 'FAILED':
                    summary += f"\n[FAILED] {r['test']}\n"
                    if r['output']:
                        out = r['output']
                        if len(out) > 2000:
                            out = out[:1000] + f"\n... [truncated, {len(out)} chars] ...\n" + out[-500:]
                        summary += f"{out}\n"

        # Post to message bus
        if message_bus:
            failed_details = []
            for r in results:
                if r['status'] == 'FAILED':
                    failed_details.append({
                        "test": r['test'],
                        "exit_code": r['exit_code'],
                        "output": r['output'][:2000] if r['output'] else "",
                    })
            message_bus.post("reproducer", "regression_test_results", {
                "phase": phase,
                "framework": framework,
                "total": len(all_tests),
                "passed": passed_count,
                "failed": failed_count,
                "failed_details": failed_details,
                "summary": f"{passed_count}P/{failed_count}F of {len(available_tests)} tests",
            })

        return summary
    return run_regression_tests, register_regression_tests


def make_run_command(docker_env, *, trajectory_callback=None, context_callback=None):
    """Create a run_command tool for executing shell commands in the container.

    Args:
        docker_env: Docker environment instance
        trajectory_callback: Optional callable(action, action_type, input_data, output_data, metadata)
        context_callback: Optional callable(role, action, result, metadata)
    """
    @tool
    def run_command(cmd: str) -> str:
        """Run a shell command in the repo. NEVER use ls -R or find without limits — large output wastes tokens."""
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"

        returncode, stdout, stderr = docker_env.run_command(
            f"cd {docker_env.repo_path} && {cmd}", timeout=60
        )

        # Cap output to avoid blowing up context
        max_chars = 5000
        if stdout and len(stdout) > max_chars:
            stdout = stdout[:max_chars] + f"\n... [truncated, {len(stdout)} chars total]"
        if stderr and len(stderr) > max_chars:
            stderr = stderr[:max_chars] + f"\n... [truncated, {len(stderr)} chars total]"

        result = f"Exit code: {returncode}\n"
        if stdout:
            result += f"Output:\n{stdout}\n"
        if stderr:
            result += f"Stderr:\n{stderr}\n"

        if trajectory_callback:
            trajectory_callback(
                "run_command", "tool_call",
                {"cmd": cmd}, result,
                {"exit_code": returncode}
            )
        if context_callback:
            context_callback("reproducer", "run_command", result, {"exit_code": returncode, "command": cmd})

        return result
    return run_command


def make_apply_patch(docker_env, *, message_bus=None, shared_context=None):
    """Create an apply_patch tool that fetches and applies the latest patch from the patch editor.

    Args:
        docker_env: Docker environment instance
        message_bus: AgentMessageBus for reading patch data
        shared_context: SharedContextManager for fallback patch lookup
    """
    import base64 as _b64

    def _is_valid_unified_diff(text: str) -> bool:
        stripped = text.strip()
        return stripped.startswith("diff ") or "--- a/" in stripped or "+++ b/" in stripped

    def _find_best_patch():
        if message_bus:
            for m in reversed(message_bus.read(msg_type="patch_generated")):
                candidate = str(m["data"])
                if _is_valid_unified_diff(candidate):
                    logger.info("[apply_patch] Found valid diff in message bus")
                    return candidate
        if shared_context:
            try:
                ctx = shared_context._load_context()
                diff = ctx.get("patch_editor", {}).get("unified_diff", "")
                if diff and _is_valid_unified_diff(diff):
                    logger.info("[apply_patch] Found valid diff in shared context")
                    return diff
            except Exception:
                pass
        return None

    @tool
    def apply_patch() -> str:
        """Apply the latest patch from the patch editor to your container for testing.
        Will wait up to 300 seconds for the patch editor to post a valid diff."""
        import time as _time

        if not message_bus:
            return "ERROR: No message bus available"
        if not docker_env:
            return "ERROR: No Docker environment available"

        # Wait for a valid unified diff from the patch editor
        patch_str = _find_best_patch()
        if not patch_str:
            logger.info("[apply_patch] No valid diff yet, waiting for patch editor...")
            for _ in range(60):  # up to 300 seconds (60 x 5s)
                _time.sleep(5)
                patch_str = _find_best_patch()
                if patch_str:
                    break
        if not patch_str:
            return (
                "No valid unified diff found after waiting 300s. The patch editor may not have "
                "finished yet, or only shared a text description."
            )

        encoded_diff = _b64.b64encode(patch_str.encode()).decode()

        # Reset to clean state before applying new patch
        docker_env.run_command(
            f"cd {docker_env.repo_path} && git checkout -- . 2>&1", timeout=15
        )

        strategies = [
            ("loose",  "git apply -v --ignore-whitespace --ignore-space-change -C1"),
            ("3way",   "git apply -v --3way"),
            ("patch",  "patch -p1 --forward --fuzz=3"),
        ]

        output = ""
        for label, cmd in strategies:
            apply_cmd = (
                f"cd {docker_env.repo_path} && echo '{encoded_diff}' | base64 -d | {cmd} 2>&1"
            )
            rc, stdout, stderr = docker_env.run_command(apply_cmd, timeout=30)
            output = (stdout or "") + (stderr or "")
            if rc == 0:
                logger.info(f"[apply_patch] Patch applied successfully ({label})")
                return (
                    f"Patch applied successfully to {docker_env.repo_path} (strategy: {label}).\n"
                    f"{output}\n"
                    f"You can now re-run your reproduction script and regression tests."
                )
            logger.warning(f"[apply_patch] ({label}) failed: {output}")
            if label != strategies[-1][0]:
                docker_env.run_command(
                    f"cd {docker_env.repo_path} && git checkout -- . 2>&1", timeout=15
                )

        return f"ERROR: Failed to apply patch (tried all strategies):\n{output}"
    return apply_patch


# ---------------------------------------------------------------------------
# Planner-specific tools (used by RolePlanner in graph_builder)
# ---------------------------------------------------------------------------

def make_run_tests(docker_env, *, instance_id: str, detect_framework_fn=None, test_commands: dict = None):
    """Create a run_tests tool for running regression tests on a specific path.

    Args:
        docker_env: Docker environment instance
        instance_id: SWE-bench instance ID for framework detection
        detect_framework_fn: Optional framework detection function
        test_commands: Dict mapping framework names to test commands
    """
    _default_commands = {
        "pytest": "python -m pytest -xvs",
        "django": "./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1",
        "sympy": "./bin/test -C --verbose",
        "sphinx": "tox --current-env -epy39 -v --",
        "jest": "npx jest --verbose",
        "mocha": "npx mocha --reporter spec",
        "npm": "npm test --",
        "maven": "mvn test -pl . -Dtest=",
        "gradle": "./gradlew test --tests",
        "go": "go test -v -run",
        "cargo": "cargo test",
    }
    if test_commands is None:
        test_commands = _default_commands

    @tool
    def run_tests(test_path: str) -> str:
        """Run tests for a specific test file or directory.

        Args:
            test_path: Path to test file or directory relative to repo root
        """
        if docker_env is None:
            return "[ERROR] Docker environment not initialized"
        if detect_framework_fn:
            framework = detect_framework_fn(instance_id)
        else:
            instance_lower = instance_id.lower()
            if instance_lower.startswith("django"):
                framework = "django"
            elif instance_lower.startswith("sympy"):
                framework = "sympy"
            elif instance_lower.startswith("sphinx"):
                framework = "sphinx"
            else:
                # Auto-detect non-Python frameworks
                framework = "pytest"  # default
                for check_file, fw in [
                    ("package.json", "npm"), ("pom.xml", "maven"),
                    ("build.gradle", "gradle"), ("build.gradle.kts", "gradle"),
                    ("go.mod", "go"), ("Cargo.toml", "cargo"),
                ]:
                    rc, out, _ = docker_env.run_command(
                        f"test -f {docker_env.repo_path}/{check_file} && echo 'yes'", timeout=5)
                    if 'yes' in out:
                        if fw == "npm":
                            rc2, jest_out, _ = docker_env.run_command(
                                f"grep -q 'jest' {docker_env.repo_path}/package.json && echo 'jest'", timeout=5)
                            framework = "jest" if 'jest' in jest_out else "npm"
                        else:
                            framework = fw
                        break
        base_cmd = test_commands.get(framework, "python -m pytest -xvs")
        cmd = f"cd {docker_env.repo_path} && {base_cmd} {test_path} 2>&1 | tail -80"
        logger.info(f"[run_tests] Running: {cmd}")
        returncode, stdout, stderr = docker_env.run_command(cmd, timeout=120)
        output = stdout or stderr or "(no output)"
        if len(output) > 3000:
            output = output[-3000:]
        status = "PASSED" if returncode == 0 else "FAILED"
        return f"[Tests {status} (exit code {returncode})]\n{output}"
    return run_tests


def make_validate_entities(docker_env):
    """Create a validate_entities tool that checks if files/functions exist in the repo."""
    _cache: dict = {}

    @tool
    def validate_entities(entities_json: str) -> str:
        """Check if files and functions exist in the repo.

        Args:
            entities_json: JSON with 'files' (list) and 'functions' (list of {name, file})
        """
        import hashlib as _hashlib

        if docker_env is None:
            return "[ERROR] Docker environment not initialized"

        cache_key = _hashlib.md5(entities_json.encode()).hexdigest()
        if cache_key in _cache:
            logger.info("[validate_entities] cache hit")
            return _cache[cache_key]

        try:
            entities = _json.loads(entities_json)
        except _json.JSONDecodeError as e:
            return f'[ERROR] Invalid JSON: {e}. Expected: {{"files": [...], "functions": [{{"name": "...", "file": "..."}}]}}'

        file_paths = entities.get("files", [])
        func_entries = entities.get("functions", [])
        results = ["=== ENTITY VALIDATION REPORT ===\n", "FILE VALIDATION:"]

        for fp in file_paths:
            if not fp:
                continue
            normalized = fp.lstrip("./").replace(f"{docker_env.repo_path}/", "")
            full_path = f"{docker_env.repo_path}/{normalized}"
            rc, stdout, _ = docker_env.run_command(
                f"test -f {full_path} && echo 'exists' || echo 'missing'", timeout=5
            )
            if stdout.strip() == "exists":
                results.append(f"  [OK] {normalized}")
            else:
                basename = normalized.split("/")[-1]
                rc2, stdout2, _ = docker_env.run_command(
                    f"cd {docker_env.repo_path} && find . -name '{basename}' -type f 2>/dev/null | head -5",
                    timeout=15
                )
                suggestions = []
                if rc2 == 0 and stdout2.strip():
                    suggestions = [s.lstrip("./") for s in stdout2.strip().split("\n")]
                if suggestions:
                    results.append(f"  [NOT FOUND] {normalized} — did you mean: {', '.join(suggestions)}")
                else:
                    results.append(f"  [NOT FOUND] {normalized} — no similar file found in repo")

        results.append("\nFUNCTION VALIDATION:")
        for entry in func_entries:
            if isinstance(entry, str):
                func_name = entry
                func_file = ""
            elif isinstance(entry, dict):
                func_name = entry.get("name", "")
                func_file = entry.get("file", "")
            else:
                continue
            if not func_name:
                continue
            grep_name = func_name.split(".")[-1] if "." in func_name else func_name
            if func_file:
                normalized_file = func_file.lstrip("./").replace(f"{docker_env.repo_path}/", "")
                full_fp = f"{docker_env.repo_path}/{normalized_file}"
                rc, stdout, _ = docker_env.run_command(
                    f"grep -n 'def {grep_name}\\|class {grep_name}' {full_fp} 2>/dev/null | head -3",
                    timeout=10
                )
                if rc == 0 and stdout.strip():
                    results.append(f"  [OK] {func_name} in {normalized_file} — found at: {stdout.strip().split(chr(10))[0]}")
                else:
                    results.append(f"  [NOT FOUND] {func_name} in {normalized_file}")
            else:
                rc, stdout, _ = docker_env.run_command(
                    f"cd {docker_env.repo_path} && grep -rn --include='*.py' --include='*.java' --include='*.js' --include='*.ts' --include='*.go' --include='*.rs' --include='*.c' --include='*.cpp' --include='*.h' --include='*.rb' 'def {grep_name}\\|class {grep_name}\\|func {grep_name}\\|function {grep_name}' . 2>/dev/null | grep -v test | head -5",
                    timeout=20
                )
                if rc == 0 and stdout.strip():
                    locations = stdout.strip().split("\n")
                    results.append(f"  [FOUND] {func_name} — defined at:")
                    for loc in locations[:3]:
                        results.append(f"    {loc.lstrip('./')}")
                else:
                    results.append(f"  [NOT FOUND] {func_name} — not found in repo (excluding tests)")

        results.append("\n=== END VALIDATION ===")
        report = "\n".join(results)
        _cache[cache_key] = report
        logger.info("[validate_entities] completed")
        return report
    return validate_entities


def make_check_plan_quality(model, *, role: str):
    """Create a check_plan_quality tool that uses an LLM to score a plan.

    Args:
        model: LangChain chat model instance
        role: The planner role (localizer, patch_editor, reproducer)
    """
    _cache: dict = {}

    @tool
    def check_plan_quality(plan: str) -> str:
        """Score your plan and get improvement suggestions before submitting.

        Args:
            plan: JSON string of the plan to evaluate
        """
        import hashlib as _hashlib

        try:
            plan_obj = _json.loads(plan)
        except _json.JSONDecodeError as e:
            return f"[ERROR] Invalid JSON plan: {e}"

        cache_key = _hashlib.md5(plan.encode()).hexdigest()
        if cache_key in _cache:
            logger.info(f"[check_plan_quality-{role}] cache hit")
            return _cache[cache_key]

        # If a previous plan already scored HIGH, skip
        for prev_result in _cache.values():
            if "Quality is HIGH" in prev_result:
                logger.info(f"[check_plan_quality-{role}] Previous plan scored HIGH — skipping")
                short = f"=== PLAN QUALITY CHECK ({plan_obj.get('role', role)}) ===\nPrevious version already scored HIGH. Submit your plan via submit_plan()."
                _cache[cache_key] = short
                return short

        if not isinstance(plan_obj, dict) or "steps" not in plan_obj:
            return "[ERROR] Plan must be a JSON object with a 'steps' field"

        plan_role = plan_obj.get("role", role)

        quality_criteria = {
            "localizer": (
                "LOCALIZER PLAN QUALITY CRITERIA:\n"
                "1. concrete_paths (0-1): Are file paths clear, concrete, and specific?\n"
                "2. buggy_function_clarity (0-1): Is a specific buggy function identified?\n"
                "3. specific_code_area (0-1): Does it narrow to a specific code area?\n"
                "4. call_chain_coverage (0-1): Does it trace the full call chain from public API to internal functions?\n"
                "5. step_completeness (0-1): Are all necessary inspection/trace steps included?\n"
            ),
            "patch_editor": (
                "PATCH EDITOR PLAN QUALITY CRITERIA:\n"
                "1. concrete_paths (0-1): Are file paths specific for files to modify?\n"
                "2. buggy_function_clarity (0-1): Is a specific function to modify identified?\n"
                "3. specific_modification (0-1): Is the modification detailed and specific (exact code change)?\n"
                "4. multi_file_coverage (0-1): Does it cover ALL files needing changes?\n"
                "5. step_completeness (0-1): Are inspect + modify steps included for each fix?\n"
            ),
            "reproducer": (
                "REPRODUCER PLAN QUALITY CRITERIA:\n"
                "1. concrete_paths (0-1): Are clear paths to functions to exercise provided?\n"
                "2. expected_behavior (0-1): Does it describe expected behavior after fix?\n"
                "3. test_design (0-1): Does it specify how to test and what assertions to make?\n"
                "4. uses_regression_tests (0-1): Does it reference existing regression tests?\n"
                "5. failure_description (0-1): Is the expected failure mode described?\n"
            ),
        }

        criteria = quality_criteria.get(plan_role, quality_criteria["localizer"])

        prompt = (
            f"Evaluate the quality of this {plan_role} plan for a bug-fixing system.\n\n"
            f"PLAN:\n{_json.dumps(plan_obj, indent=2)}\n\n"
            f"{criteria}\n"
            f"Return ONLY valid JSON with:\n"
            f"- scores: object with each criterion name mapped to a 0.0-1.0 score\n"
            f"- overall: single 0.0-1.0 score\n"
            f"- status: 'HIGH' (>=0.7), 'MEDIUM' (>=0.4), or 'LOW' (<0.4)\n"
            f"- issues: list of specific problems found\n"
            f"- suggestions: list of specific improvements to make\n"
        )

        try:
            resp = model.invoke([
                SystemMessage(content="You evaluate bug-fixing plan quality. Return ONLY valid JSON."),
                HumanMessage(content=prompt),
            ])
            txt = resp.content if isinstance(resp.content, str) else str(resp.content)
            if "```" in txt:
                match = _re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', txt)
                if match:
                    txt = match.group(1).strip()
            json_match = _re.search(r'\{.*\}', txt, _re.DOTALL)
            if json_match:
                txt = json_match.group(0)

            result = _json.loads(txt)
            overall = result.get("overall", 0.0)
            status = result.get("status", "LOW")
            issues = result.get("issues", [])
            suggestions = result.get("suggestions", [])

            report = f"=== PLAN QUALITY CHECK ({plan_role}) ===\n"
            report += f"Overall: {overall:.2f} — {status}\n\n"

            scores = result.get("scores", {})
            if scores:
                report += "Scores:\n"
                for k, v in scores.items():
                    report += f"  {k}: {v:.2f}\n"
                report += "\n"

            if issues:
                report += "Issues:\n"
                for i, issue in enumerate(issues, 1):
                    report += f"  {i}. {issue}\n"
                report += "\n"

            if suggestions:
                report += "Suggestions:\n"
                for i, sug in enumerate(suggestions, 1):
                    report += f"  {i}. {sug}\n"

            if status == "HIGH":
                report += "\nQuality is HIGH — plan is ready for submission via submit_plan()."
            elif status == "MEDIUM":
                report += "\nQuality is MEDIUM — consider addressing the suggestions, then submit via submit_plan()."
            else:
                report += "\nQuality is LOW — please refine the plan based on suggestions before submitting."

            logger.info(f"[check_plan_quality-{role}] {status} ({overall:.2f})")
            _cache[cache_key] = report
            return report

        except Exception as e:
            logger.warning(f"[check_plan_quality-{role}] failed: {e}")
            return f"[ERROR] Quality check failed: {e}. Submit your plan anyway via submit_plan()."
    return check_plan_quality


def make_submit_plan(*, role: str, on_submit):
    """Create a submit_plan tool.

    Args:
        role: The planner role name
        on_submit: Callback(plan_str) called when plan is submitted
    """
    @tool
    def submit_plan(plan: str) -> str:
        """Submit your final plan as a JSON string.

        Args:
            plan: JSON string of the plan
        """
        try:
            plan_obj = _json.loads(plan)
        except _json.JSONDecodeError:
            # Try to repair common JSON issues: unescaped backslashes, trailing commas
            import re as _re
            fixed = plan
            fixed = _re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', fixed)  # fix unescaped backslashes
            fixed = _re.sub(r',\s*([}\]])', r'\1', fixed)  # remove trailing commas
            try:
                plan_obj = _json.loads(fixed)
                plan = fixed
            except _json.JSONDecodeError as e:
                return f"[ERROR] Plan is not valid JSON: {e}"

        if not isinstance(plan_obj, dict):
            return f"[ERROR] Plan must be a JSON object, got {type(plan_obj).__name__}"

        on_submit(plan)
        return f"PLAN_SUBMITTED: Your {role} plan has been recorded."
    return submit_plan
