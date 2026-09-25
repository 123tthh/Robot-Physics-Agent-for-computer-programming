"""Optional local Codex process adapter; completion is never test evidence."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import signal

from ..domain import Evidence, EvidenceDomain
from .virtual_computer import IndependentTestExecutor


class LocalCodexAdapter:
    def __init__(self, workspace: Path, *, executable='codex', timeout_s=300.):
        self.workspace = Path(workspace).resolve()
        self.executable = executable
        self.timeout_s = timeout_s

    def readiness(self):
        return {'executable_available': shutil.which(self.executable) is not None,
                'workspace_exists': self.workspace.is_dir(), 'authentication_checked': False,
                'model_executed': False}

    async def submit(self, prompt: str) -> tuple[Evidence, ...]:
        if not prompt.strip():
            raise ValueError('code task prompt is empty')
        if not self.workspace.is_dir():
            raise ValueError('code workspace must already exist')
        process = await asyncio.create_subprocess_exec(
            self.executable, 'exec', '--sandbox', 'workspace-write', '--skip-git-repo-check',
            '--ephemeral', '--json', '--cd', str(self.workspace), '-',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(prompt.encode()), timeout=self.timeout_s)
        except (TimeoutError, asyncio.CancelledError):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            raise
        result = {'exit_code': process.returncode, 'events_jsonl': stdout.decode(errors='replace'),
                  'stderr': stderr.decode(errors='replace'), 'independent_tests_passed': False}
        return (Evidence(EvidenceDomain.SOFTWARE, 'code_agent_response', result, 'local_codex_process'),)

    async def request_fix(self, failure_evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
        return await self.submit('Repair the implementation against this independent failure evidence. '
                                 'Do not modify the test specification.\n' +
                                 json.dumps([e.to_dict() for e in failure_evidence]))


class LocalTestRunnerAdapter:
    """TestRunnerAdapter implementation, separate from the code agent process."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()

    async def run(self, specification) -> tuple[Evidence, ...]:
        result = await asyncio.to_thread(IndependentTestExecutor().run,
            specification['argv'], self.workspace, timeout_s=specification.get('timeout_s', 30.))
        return (Evidence(EvidenceDomain.SOFTWARE, 'process_exit_code', result['exit_code'], 'independent_process_exit'),
                Evidence(EvidenceDomain.SOFTWARE, 'test_process_result', result, 'independent_process_exit'))
