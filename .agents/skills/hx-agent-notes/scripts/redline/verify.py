#!/usr/bin/env -S uv run
"""Strict note gate: exact graph, AST placement, diff pairing, and machine-readable diagnostics."""
import argparse
from collections import Counter
import json
import sys
from pathlib import Path, PurePosixPath

from snapshot import CONFIG, Snapshot, changed_paths, comparison_config, git, guarded, protected_resource


def arguments():
    parser = argparse.ArgumentParser(description='严格校验决策双链, AST 声明锚点及 diff 配对, 可输出完整 JSON 诊断')
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--all', action='store_true', help='检查完整工作区及待提交 diff 的双端配对')
    mode.add_argument('--diff', action='store_true', help='检查改动文件及其完整关联决策范围 (默认)')
    parser.add_argument('--base', help='比较基线提交; PR 传入 merge-base')
    parser.add_argument('--head', help='读取指定提交的精确文件树')
    parser.add_argument('--staged', action='store_true', help='读取精确暂存区')
    parser.add_argument('--json', type=Path, help='完整 JSON 路径, 默认写入 Git 目录 reports/agent-notes-<模式>.json')
    return parser.parse_args()


def check(args):
    from graph import Graph, affected_paths, diagnostic, directory_coverage, paired_changes
    repo = Path(git(args.repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if args.staged and args.head:
        raise ValueError('--staged 与 --head 不能同时使用')
    after = Snapshot(repo, args.head, args.staged)
    config = after.config()
    if args.base:
        base = git(repo, 'rev-parse', '--verify', args.base + '^{tree}').decode().strip()
    else:
        try:
            base = git(repo, 'rev-parse', '--verify', 'HEAD^{tree}').decode().strip()
        except ValueError:
            branch = git(repo, 'symbolic-ref', '--quiet', 'HEAD').decode().strip()
            refs = git(repo, 'for-each-ref', '--format=%(refname)', branch).decode().splitlines()
            if branch in refs:
                raise ValueError('HEAD 无效; 拒绝使用空比较')
            base = git(repo, 'hash-object', '-t', 'tree', '--stdin', input=b'').decode().strip()
    before = Snapshot(repo, base)
    changed = changed_paths(before, after)
    old_config, migration = comparison_config(before, config, changed)
    # Read the union so removing a guarded directory cannot hide changed code
    config = dict(config, guarded=sorted(set(config['guarded']) | set(old_config['guarded'])))
    previous = Graph(before, old_config, set())
    metadata = Graph(after, config, set())
    directories = {str(PurePosixPath(p).parent) for p in changed if guarded(p, config)}
    changed_directories = set(directories)
    for note in [*previous.notes.values(), *metadata.notes.values()]:
        if note.path in changed or note.directories & changed_directories:
            directories.update(note.directories)
    current = Graph(after, config, None if args.all or CONFIG in changed else directories)
    affected = affected_paths(previous, current, changed)
    issues = [i for i in current.issues if args.all or CONFIG in changed or i['path'] in affected
              or set(i['related']) & affected]
    # Deleted or malformed previous edges must not make paired-change obligations disappear
    for issue in previous.issues:
        if issue['rule'] == 'note-format' and issue['path'] in changed:
            issues.append(diagnostic('baseline-review', issue['path'], '旧版或格式不合法的决策记录已改动, 请审核迁移结果', severity='review'))
    issues += paired_changes(previous, current, changed)
    issues += [diagnostic('resource-review', path,
               '受保护资源已改动, 其格式不在 AST 支持范围内; 请审核对应决策及引用',
               severity='review') for path in sorted(changed) if protected_resource(path, config)]
    if args.all or CONFIG in changed:
        issues += directory_coverage(current)
    if CONFIG in changed and CONFIG in before.modes:
        issues.append(diagnostic('policy-review', CONFIG, '受保护目录策略已改动; 需要独立审核', severity='review'))
    if migration:
        issues.append(diagnostic('migration-review', CONFIG, '本次显式迁移 v1 到 v2; 比较仍包含旧版保护范围, 请审核迁移结果', severity='review'))
    try:
        base_commit = git(repo, 'rev-parse', '--verify', (args.base or 'HEAD') + '^{commit}').decode().strip()
    except ValueError:
        base_commit = None
    deleted = sorted(changed & (set(before.modes) - set(after.modes)))
    return dict(version=2, mode='all' if args.all else 'diff', base=base, base_commit=base_commit,
                head=args.head, changed=sorted(changed), deleted=deleted, issues=issues, ok=not issues)


# Separate review notices from actionable structural blockers
# .agents/notes/implemented/process/2026-10-10-human-review-diagnostics-are-advisory.md
def has_blocking_issues(issues):
    advisory = {'baseline-review', 'resource-review', 'policy-review', 'migration-review'}
    return any(issue['severity'] != 'review' or issue['rule'] not in advisory for issue in issues)


# Save full diagnostics before selecting console detail
# .agents/notes/implemented/process/2026-10-10-gates-save-full-reports.md
def main():
    args = arguments()
    try:
        result = check(args)
        code = 1 if has_blocking_issues(result['issues']) else 0
    except (OSError, ValueError, UnicodeError, ImportError) as exc:
        result = dict(version=2, ok=False, head=args.head, issues=[dict(rule='gate-error', path=CONFIG,
                      line=1, message=f'门禁无法完成: {exc}', severity='error', related=[])])
        code = 2
    try:
        output = args.json
        if output is None:
            mode = 'staged' if args.staged else 'all' if args.all else 'diff'
            location = git(args.repo, 'rev-parse', '--path-format=absolute', '--git-path',
                           f'reports/agent-notes-{mode}.json').decode().strip()
            output = Path(location)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    except (OSError, ValueError) as exc:
        print(f'Agent Notes: 1 项错误, 类型 report-write-error, 无法保存完整报告: {exc}')
        return 2
    if len(result['issues']) <= 10:
        for issue in result['issues']:
            level = '错误' if issue['severity'] == 'error' else '待审核'
            print(f"{level} {issue['path']}:{issue['line']} [{issue['rule']}] {issue['message']}")
    errors = sum(issue['severity'] == 'error' for issue in result['issues'])
    status = '通过' if result['ok'] else '需处理' if code else '待人工审核'
    print(f"Agent Notes: {status}, {errors} 项错误, {len(result['issues']) - errors} 项待审核")
    counts = Counter(issue['rule'] for issue in result['issues'])
    if counts:
        print('类型: ' + ', '.join(f'{rule}={count}' for rule, count in sorted(counts.items())))
    print(f'完整报告: {output.resolve()}')
    return code


if __name__ == '__main__':
    sys.exit(main())
