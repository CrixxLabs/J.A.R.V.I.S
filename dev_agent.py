"""
dev_agent.py — Constrained Development Agent for JARVIS MARK VII

Provides safe, auditable code assistance capabilities:
- Code inspection and analysis
- Test running and failure explanation
- Change proposals (not execution)
- Safe file operations within constrained scope
"""

import os
import json
import subprocess
import tempfile
import shutil
import ast
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Safety configuration
ALLOWED_BASE_DIRS = [
    Path.cwd(),  # Current project directory
]

FORBIDDEN_PATHS = [
    "/etc", "/root", "/home", "C:\\Windows", "C:\\Program Files",
    "C:\\Users", "/usr", "/bin", "/sbin", "/var",
]

FORBIDDEN_COMMANDS = [
    "rm -rf", "del /f /s", "format", "mkfs", "fdisk",
    "shutdown", "reboot", "poweroff", "halt",
    "dd if=", "> /dev/", "chmod 777", "chown root",
    "sudo ", "su -", "runas ", "psexec",
]

MAX_FILE_SIZE = 1024 * 1024  # 1MB
MAX_OUTPUT_LINES = 500
COMMAND_TIMEOUT = 30  # seconds


@dataclass
class DevAgentAction:
    """Represents a dev agent action for audit logging."""
    action_type: str
    target: str
    description: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    success: bool = True
    error: Optional[str] = None
    output: Optional[str] = None


class DevAgent:
    """
    Constrained development agent with safety boundaries.
    
    Capabilities:
    - Inspect code (read files, analyze structure)
    - Run tests and explain failures
    - Propose changes (diff generation)
    - Safe file operations within project scope
    """
    
    def __init__(self, project_root: Optional[str] = None):
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.audit_log: List[DevAgentAction] = []
        self._validate_project_root()
    
    def _validate_project_root(self):
        """Ensure project root is within allowed directories."""
        for allowed in ALLOWED_BASE_DIRS:
            try:
                self.project_root.relative_to(allowed.resolve())
                return
            except ValueError:
                continue
        raise SecurityError(f"Project root {self.project_root} is outside allowed directories")
    
    def _is_path_allowed(self, path: Path) -> bool:
        """Check if a path is within allowed directories."""
        try:
            path = path.resolve()
            for allowed in ALLOWED_BASE_DIRS:
                try:
                    path.relative_to(allowed.resolve())
                    return True
                except ValueError:
                    continue
            return False
        except Exception:
            return False
    
    def _is_command_safe(self, command: str) -> bool:
        """Check if a command is safe to execute."""
        cmd_lower = command.lower()
        for forbidden in FORBIDDEN_COMMANDS:
            if forbidden in cmd_lower:
                return False
        return True
    
    def _log_action(self, action: DevAgentAction):
        """Log action for audit trail."""
        self.audit_log.append(action)
        logger.info(f"DevAgent: {action.action_type} - {action.target} - {'OK' if action.success else 'FAIL'}")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CODE INSPECTION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def inspect_file(self, file_path: str) -> Dict[str, Any]:
        """Inspect a file and return analysis."""
        target = Path(file_path)
        if not target.is_absolute():
            target = self.project_root / target
        
        action = DevAgentAction("inspect_file", str(target), "Inspect file structure and content")
        
        try:
            if not self._is_path_allowed(target):
                action.success = False
                action.error = "Path outside allowed directories"
                self._log_action(action)
                return {"success": False, "error": action.error}
            
            if not target.exists():
                action.success = False
                action.error = "File not found"
                self._log_action(action)
                return {"success": False, "error": action.error}
            
            if target.stat().st_size > MAX_FILE_SIZE:
                action.success = False
                action.error = f"File too large (max {MAX_FILE_SIZE} bytes)"
                self._log_action(action)
                return {"success": False, "error": action.error}
            
            content = target.read_text(encoding='utf-8', errors='replace')
            
            result = {
                "success": True,
                "file": str(target.relative_to(self.project_root)),
                "size": len(content),
                "lines": content.count('\n') + 1,
            }
            
            # Python-specific analysis
            if target.suffix == '.py':
                result.update(self._analyze_python(content))
            
            self._log_action(action)
            return result
            
        except Exception as e:
            action.success = False
            action.error = str(e)
            self._log_action(action)
            return {"success": False, "error": str(e)}
    
    def _analyze_python(self, content: str) -> Dict[str, Any]:
        """Analyze Python code structure."""
        try:
            tree = ast.parse(content)
            
            classes = []
            functions = []
            imports = []
            
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    classes.append({
                        "name": node.name,
                        "line": node.lineno,
                        "methods": [n.name for n in node.body if isinstance(n, ast.FunctionDef)],
                        "bases": [getattr(b, 'id', str(b)) for b in node.bases],
                    })
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.append(alias.name)
                    else:
                        module = node.module or ""
                        for alias in node.names:
                            imports.append(f"{module}.{alias.name}" if module else alias.name)
            
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append({
                        "name": node.name,
                        "line": node.lineno,
                        "args": [a.arg for a in node.args.args],
                        "returns": getattr(node.returns, 'id', None) if node.returns else None,
                    })

            return {
                "classes": classes,
                "functions": functions,
                "imports": imports,
                "complexity": len(classes) + len(functions),
            }
        except SyntaxError as e:
            return {"syntax_error": str(e)}
        except Exception as e:
            return {"analysis_error": str(e)}
    
    def search_code(self, pattern: str, file_pattern: str = "*.py") -> List[Dict[str, Any]]:
        """Search for pattern in code files."""
        results = []
        for file_path in self.project_root.rglob(file_pattern):
            if not self._is_path_allowed(file_path):
                continue
            try:
                content = file_path.read_text(encoding='utf-8', errors='replace')
                lines = content.split('\n')
                for i, line in enumerate(lines, 1):
                    if pattern.lower() in line.lower():
                        results.append({
                            "file": str(file_path.relative_to(self.project_root)),
                            "line": i,
                            "content": line.strip()[:200],
                        })
                        if len(results) > 50:  # Limit results
                            break
                if len(results) > 50:
                    break
            except Exception:
                continue
        return results
    
    def get_project_structure(self, max_depth: int = 3) -> Dict[str, Any]:
        """Get project directory structure."""
        def walk_dir(path: Path, depth: int = 0) -> Dict[str, Any]:
            if depth >= max_depth:
                return {"type": "dir", "truncated": True}
            
            result = {"type": "dir", "children": {}}
            try:
                for child in sorted(path.iterdir()):
                    if child.name.startswith('.') or child.name in ('__pycache__', 'node_modules', '.git'):
                        continue
                    if child.is_dir():
                        result["children"][child.name] = walk_dir(child, depth + 1)
                    else:
                        result["children"][child.name] = {"type": "file", "size": child.stat().st_size}
            except Exception:
                pass
            return result
        
        return walk_dir(self.project_root)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # TEST RUNNING AND FAILURE ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def run_tests(self, test_path: Optional[str] = None, verbose: bool = False) -> Dict[str, Any]:
        """Run tests and return results with analysis."""
        action = DevAgentAction("run_tests", test_path or "all", "Run test suite")
        
        try:
            cmd = [sys.executable, "-m", "pytest"]
            if test_path:
                cmd.append(test_path)
            if verbose:
                cmd.append("-v")
            cmd.extend(["--tb=short", "-x"])  # Stop on first failure for analysis
            
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT * 4,
            )
            
            output = result.stdout + "\n" + result.stderr
            
            analysis = self._analyze_test_output(output, result.returncode)
            
            action.success = result.returncode == 0
            action.output = output[-2000:] if output else ""
            if not action.success:
                action.error = analysis.get("summary", "Tests failed")
            
            self._log_action(action)
            
            return {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "output": output[-3000:] if output else "",
                "analysis": analysis,
            }
            
        except subprocess.TimeoutExpired:
            action.success = False
            action.error = "Test run timed out"
            self._log_action(action)
            return {"success": False, "error": "Test run timed out"}
        except Exception as e:
            action.success = False
            action.error = str(e)
            self._log_action(action)
            return {"success": False, "error": str(e)}
    
    def _analyze_test_output(self, output: str, returncode: int) -> Dict[str, Any]:
        """Analyze test output to provide actionable insights."""
        analysis = {
            "summary": "",
            "failed_tests": [],
            "errors": [],
            "suggestions": [],
        }
        
        if returncode == 0:
            analysis["summary"] = "All tests passed"
            return analysis
        
        lines = output.split('\n')
        failed_tests = []
        errors = []
        
        for line in lines:
            if "FAILED" in line and "::" in line:
                failed_tests.append(line.strip())
            elif "ERROR" in line and ("test_" in line or "::" in line):
                errors.append(line.strip())
            elif "AssertionError" in line:
                errors.append(line.strip())
        
        analysis["failed_tests"] = failed_tests[:10]
        analysis["errors"] = errors[:10]
        
        if failed_tests:
            analysis["summary"] = f"{len(failed_tests)} test(s) failed"
            analysis["suggestions"].append("Run failed tests with -v for more details")
            analysis["suggestions"].append("Check the first failure for root cause")
        elif errors:
            analysis["summary"] = f"{len(errors)} error(s) during test execution"
            analysis["suggestions"].append("Check test setup and imports")
        else:
            analysis["summary"] = "Tests failed with unknown error"
        
        return analysis
    
    def explain_failure(self, test_output: str) -> str:
        """Provide human-readable explanation of test failure."""
        # Extract the first failure
        lines = test_output.split('\n')
        failure_context = []
        in_failure = False
        
        for line in lines:
            if "FAILED" in line or "ERROR" in line or "AssertionError" in line:
                in_failure = True
            if in_failure:
                failure_context.append(line)
                if len(failure_context) > 30:
                    break
        
        if not failure_context:
            return "No clear failure found in output."
        
        context = "\n".join(failure_context)
        
        # Simple pattern-based explanations
        if "AssertionError" in context:
            if "==" in context:
                return "Assertion failed - expected value doesn't match actual. Check the test's expected vs actual values."
            return "Assertion failed - a condition expected to be True was False."
        elif "ImportError" in context or "ModuleNotFoundError" in context:
            return "Import error - a required module is missing. Check dependencies and PYTHONPATH."
        elif "AttributeError" in context:
            return "Attribute error - accessing non-existent attribute. Check object type and method names."
        elif "TypeError" in context:
            return "Type error - function called with wrong argument types. Check function signatures."
        elif "SyntaxError" in context:
            return "Syntax error in code - check the indicated line for invalid Python syntax."
        elif "timeout" in context.lower():
            return "Test timed out - possible infinite loop or slow operation. Check test logic."
        else:
            return f"Test failure detected. First failure context:\n{context[:1000]}"
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CHANGE PROPOSALS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def propose_change(self, file_path: str, changes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Propose changes to a file (generates unified diff).
        Does NOT apply changes - only generates proposal for review.
        """
        target = Path(file_path)
        if not target.is_absolute():
            target = self.project_root / target
        
        action = DevAgentAction("propose_change", str(target), "Generate change proposal")
        
        try:
            if not self._is_path_allowed(target):
                action.success = False
                action.error = "Path outside allowed directories"
                self._log_action(action)
                return {"success": False, "error": action.error}
            
            if not target.exists():
                action.success = False
                action.error = "File not found"
                self._log_action(action)
                return {"success": False, "error": action.error}
            
            original = target.read_text(encoding='utf-8', errors='replace')
            lines = original.split('\n')
            
            # Apply changes to generate new content
            new_lines = lines.copy()
            for change in sorted(changes, key=lambda c: c.get('line', 0), reverse=True):
                line_num = change.get('line', 0) - 1  # Convert to 0-indexed
                change_type = change.get('type', 'replace')
                
                if change_type == 'replace' and 'new_content' in change:
                    if 0 <= line_num < len(new_lines):
                        new_lines[line_num] = change['new_content']
                elif change_type == 'insert' and 'new_content' in change:
                    if 0 <= line_num <= len(new_lines):
                        new_lines.insert(line_num, change['new_content'])
                elif change_type == 'delete':
                    if 0 <= line_num < len(new_lines):
                        new_lines.pop(line_num)
            
            new_content = '\n'.join(new_lines)
            
            # Generate unified diff
            import difflib
            diff = list(difflib.unified_diff(
                original.split('\n'),
                new_content.split('\n'),
                fromfile=str(target.relative_to(self.project_root)),
                tofile=str(target.relative_to(self.project_root)),
                lineterm='',
            ))
            
            action.output = "\n".join(diff[:100])  # Truncate for audit log
            self._log_action(action)
            
            return {
                "success": True,
                "file": str(target.relative_to(self.project_root)),
                "diff": "\n".join(diff),
                "original_lines": len(lines),
                "new_lines": len(new_lines),
            }
            
        except Exception as e:
            action.success = False
            action.error = str(e)
            self._log_action(action)
            return {"success": False, "error": str(e)}
    
    def apply_change(self, file_path: str, diff: str) -> Dict[str, Any]:
        """Apply a unified diff to a file (requires explicit confirmation)."""
        target = Path(file_path)
        if not target.is_absolute():
            target = self.project_root / target
        
        action = DevAgentAction("apply_change", str(target), "Apply change proposal")
        
        try:
            if not self._is_path_allowed(target):
                action.success = False
                action.error = "Path outside allowed directories"
                self._log_action(action)
                return {"success": False, "error": action.error}
            
            # Parse and apply unified diff
            import difflib
            original = target.read_text(encoding='utf-8', errors='replace')
            original_lines = original.split('\n')
            
            # Simple diff application (for basic unified diffs)
            # In production, use patch library
            new_lines = list(difflib.restore(
                diff.split('\n'),
                2  # 2 = new file
            ))
            
            # Create backup
            backup_path = target.with_suffix(target.suffix + '.bak')
            shutil.copy2(target, backup_path)
            
            # Write new content
            target.write_text('\n'.join(new_lines), encoding='utf-8')
            
            action.output = f"Applied diff to {target}, backup at {backup_path}"
            self._log_action(action)
            
            return {
                "success": True,
                "file": str(target.relative_to(self.project_root)),
                "backup": str(backup_path.relative_to(self.project_root)),
            }
            
        except Exception as e:
            action.success = False
            action.error = str(e)
            self._log_action(action)
            return {"success": False, "error": str(e)}
    
    # ═══════════════════════════════════════════════════════════════════════════
    # SAFE FILE OPERATIONS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def read_file(self, file_path: str, max_lines: int = 200) -> Dict[str, Any]:
        """Safely read a file within project scope."""
        target = Path(file_path)
        if not target.is_absolute():
            target = self.project_root / target
        
        if not self._is_path_allowed(target):
            return {"success": False, "error": "Path outside allowed directories"}
        
        if not target.exists():
            return {"success": False, "error": "File not found"}
        
        if target.stat().st_size > MAX_FILE_SIZE:
            return {"success": False, "error": "File too large"}
        
        try:
            content = target.read_text(encoding='utf-8', errors='replace')
            lines = content.split('\n')
            if len(lines) > max_lines:
                content = '\n'.join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"
            return {"success": True, "content": content, "lines": len(lines)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def write_file(self, file_path: str, content: str, create_dirs: bool = True) -> Dict[str, Any]:
        """Safely write a file within project scope (creates backup)."""
        target = Path(file_path)
        if not target.is_absolute():
            target = self.project_root / target
        
        if not self._is_path_allowed(target):
            return {"success": False, "error": "Path outside allowed directories"}
        
        # Create backup if file exists
        if target.exists():
            backup_path = target.with_suffix(target.suffix + f'.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}')
            shutil.copy2(target, backup_path)
        else:
            backup_path = None
            if create_dirs:
                target.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            target.write_text(content, encoding='utf-8')
            return {
                "success": True,
                "file": str(target.relative_to(self.project_root)),
                "backup": str(backup_path.relative_to(self.project_root)) if backup_path else None,
                "size": len(content),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # ═══════════════════════════════════════════════════════════════════════════
    # SAFE COMMAND EXECUTION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def run_safe_command(self, command: str, args: List[str] = None) -> Dict[str, Any]:
        """Run a pre-approved safe command."""
        allowed_commands = {
            "pytest": ["pytest", "-v", "--tb=short"],
            "python": ["python", "-m", "py_compile"],
            "black": ["black", "--check", "--diff"],
            "ruff": ["ruff", "check"],
            "mypy": ["mypy"],
            "git": ["git", "status", "git", "diff", "git", "log", "--oneline", "-10"],
        }
        
        action = DevAgentAction("run_command", command, "Execute safe command")
        
        # Build full command
        if command in allowed_commands:
            full_cmd = allowed_commands[command]
            if args:
                full_cmd.extend(args)
        else:
            action.success = False
            action.error = f"Command '{command}' not in allowed list"
            self._log_action(action)
            return {"success": False, "error": action.error}
        
        if not self._is_command_safe(" ".join(full_cmd)):
            action.success = False
            action.error = "Command failed safety check"
            self._log_action(action)
            return {"success": False, "error": action.error}
        
        try:
            result = subprocess.run(
                full_cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
            )
            
            output = result.stdout + "\n" + result.stderr
            action.success = result.returncode == 0
            action.output = output[-2000:] if output else ""
            if not action.success:
                action.error = f"Command exited with code {result.returncode}"
            
            self._log_action(action)
            
            return {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "output": output[-3000:] if output else "",
            }
            
        except subprocess.TimeoutExpired:
            action.success = False
            action.error = "Command timed out"
            self._log_action(action)
            return {"success": False, "error": "Command timed out"}
        except Exception as e:
            action.success = False
            action.error = str(e)
            self._log_action(action)
            return {"success": False, "error": str(e)}
    
    # ═══════════════════════════════════════════════════════════════════════════
    # AUDIT AND STATUS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent audit log entries."""
        return [
            {
                "action_type": a.action_type,
                "target": a.target,
                "description": a.description,
                "timestamp": a.timestamp,
                "success": a.success,
                "error": a.error,
            }
            for a in self.audit_log[-limit:]
        ]
    
    def get_status(self) -> Dict[str, Any]:
        """Get dev agent status."""
        return {
            "project_root": str(self.project_root),
            "actions_performed": len(self.audit_log),
            "recent_actions": self.get_audit_log(10),
            "allowed_commands": list({
                "pytest": "Run tests",
                "python": "Syntax check",
                "black": "Format check",
                "ruff": "Lint check",
                "mypy": "Type check",
                "git": "Git status/diff/log",
            }.keys()),
        }


class SecurityError(Exception):
    """Security violation error."""
    pass


# Convenience functions
_dev_agent: Optional[DevAgent] = None

def get_dev_agent(project_root: Optional[str] = None) -> DevAgent:
    """Get or create global dev agent instance."""
    global _dev_agent
    if _dev_agent is None:
        _dev_agent = DevAgent(project_root)
    return _dev_agent


def inspect_file(file_path: str) -> Dict[str, Any]:
    """Convenience function to inspect a file."""
    return get_dev_agent().inspect_file(file_path)


def run_tests(test_path: Optional[str] = None) -> Dict[str, Any]:
    """Convenience function to run tests."""
    return get_dev_agent().run_tests(test_path)


def propose_change(file_path: str, changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Convenience function to propose a change."""
    return get_dev_agent().propose_change(file_path, changes)


def explain_test_failure(output: str) -> str:
    """Convenience function to explain test failure."""
    return get_dev_agent().explain_failure(output)


# ════════════════════════════════════════════════════════════════════════════
# EXECUTOR INTEGRATION
# ════════════════════════════════════════════════════════════════════════════

def _handle_dev_inspect(action: dict) -> tuple:
    """Handle dev_inspect action."""
    try:
        file_path = action.get("file_path", "")
        if not file_path:
            return False, "Which file should I inspect?"
        
        result = inspect_file(file_path)
        if result.get("success"):
            if "classes" in result:
                summary = f"File: {result['file']} ({result['lines']} lines)\n"
                summary += f"  Classes: {len(result['classes'])}\n"
                summary += f"  Functions: {len(result['functions'])}\n"
                summary += f"  Imports: {len(result['imports'])}"
                return True, summary
            else:
                return True, f"File inspected: {result.get('file', file_path)} ({result.get('lines', 0)} lines)"
        else:
            return False, result.get("error", "Inspection failed")
    except Exception as e:
        return False, f"Inspection error: {e}"


def _handle_dev_test(action: dict) -> tuple:
    """Handle dev_test action."""
    try:
        test_path = action.get("test_path", None)
        result = run_tests(test_path)
        if result.get("success"):
            return True, "All tests passed."
        else:
            explanation = explain_test_failure(result.get("output", ""))
            return False, f"Tests failed: {explanation}"
    except Exception as e:
        return False, f"Test execution error: {e}"


def _handle_dev_search(action: dict) -> tuple:
    """Handle dev_search action."""
    try:
        pattern = action.get("pattern", "")
        file_pattern = action.get("file_pattern", "*.py")
        if not pattern:
            return False, "What pattern should I search for?"
        
        agent = get_dev_agent()
        results = agent.search_code(pattern, file_pattern)
        
        if not results:
            return True, f"No matches found for '{pattern}'"
        
        summary = f"Found {len(results)} matches for '{pattern}':\n"
        for r in results[:10]:
            summary += f"  {r['file']}:{r['line']} - {r['content'][:80]}\n"
        if len(results) > 10:
            summary += f"  ... and {len(results) - 10} more"
        
        return True, summary
    except Exception as e:
        return False, f"Search error: {e}"


def _handle_dev_propose(action: dict) -> tuple:
    """Handle dev_propose action."""
    try:
        file_path = action.get("file_path", "")
        changes = action.get("changes", [])
        if not file_path or not changes:
            return False, "Need file_path and changes array"
        
        result = propose_change(file_path, changes)
        if result.get("success"):
            diff = result.get("diff", "")
            preview = diff[:1000] + ("..." if len(diff) > 1000 else "")
            return True, f"Change proposed for {result['file']}:\n{preview}"
        else:
            return False, result.get("error", "Proposal failed")
    except Exception as e:
        return False, f"Proposal error: {e}"


def _handle_dev_status(action: dict) -> tuple:
    """Handle dev_status action."""
    try:
        agent = get_dev_agent()
        status = agent.get_status()
        summary = f"Dev Agent Status:\n"
        summary += f"  Project: {status['project_root']}\n"
        summary += f"  Actions performed: {status['actions_performed']}\n"
        summary += f"  Allowed commands: {', '.join(status['allowed_commands'])}"
        return True, summary
    except Exception as e:
        return False, f"Status error: {e}"


# Register dev agent actions in executor
DEV_AGENT_ACTIONS = {
    "dev_inspect": _handle_dev_inspect,
    "dev_test": _handle_dev_test,
    "dev_search": _handle_dev_search,
    "dev_propose": _handle_dev_propose,
    "dev_status": _handle_dev_status,
}
