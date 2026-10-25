"""Verify complete report files and bounded terminal output."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_gate import GATE, NOTE, GitFixture, note


class Output(GitFixture):
    def invoke(self, *args):
        return subprocess.run([sys.executable, str(GATE), '--repo', str(self.repo), *args],
                              capture_output=True, text=True)

    def test_default_report_and_threshold(self):
        """
        Check real diagnostics at the display boundary
        .agents/notes/implemented/process/2026-10-10-gates-save-full-reports.md
        """
        for count in (0, 10, 11):
            with self.subTest(count=count):
                self.write(NOTE, note() + '\nBound the changed directory\n')
                for index in range(count):
                    self.write(f'src/bad{index}.py', 'def invalid(:\n')
                if count == 0:
                    self.write(NOTE, note())
                result = self.invoke()
                output = self.repo / '.git/reports/agent-notes-diff.json'
                report = json.loads(output.read_text())
                self.assertEqual(len(report['issues']), count, report)
                self.assertEqual(result.returncode, int(count > 0))
                self.assertEqual('src/bad0.py' in result.stdout, 0 < count <= 10)
                self.assertIn(str(output), result.stdout)
                for index in range(count):
                    (self.repo / f'src/bad{index}.py').unlink()

    def test_tool_error_and_report_write_error(self):
        result = self.invoke('--base', 'missing-ref')
        self.assertEqual(result.returncode, 2)
        data = json.loads((self.repo / '.git/reports/agent-notes-diff.json').read_text())
        self.assertEqual(data['issues'][0]['rule'], 'gate-error')
        result = self.invoke('--json', str(self.repo / 'src'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('report-write-error', result.stdout)

    def test_staged_default_is_separate(self):
        result = self.invoke('--staged')
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.repo / '.git/reports/agent-notes-staged.json').is_file())
