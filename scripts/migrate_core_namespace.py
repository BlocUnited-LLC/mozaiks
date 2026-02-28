#!/usr/bin/env python3
"""
Migration script: Rename 'core.*' imports to 'mozaiksai.core.*'

This script:
1. Finds all Python files in the repository
2. Replaces 'from core.' with 'from mozaiksai.core.'
3. Replaces 'import core.' with 'import mozaiksai.core.'
4. Reports what was changed

Run from repository root:
    python scripts/migrate_core_namespace.py
"""

import os
import re
from pathlib import Path
from typing import List, Tuple

# Repository root
REPO_ROOT = Path(__file__).parent.parent

# Directories to skip
SKIP_DIRS = {
    '.venv', 'venv', 'node_modules', '.git', '__pycache__', 
    'site', '.pytest_cache', '.ruff_cache', 'build', 'dist'
}

# Patterns to replace
PATTERNS = [
    # from core.X import Y  →  from mozaiksai.core.X import Y
    (r'^(\s*)from core\.', r'\1from mozaiksai.core.'),
    # import core.X  →  import mozaiksai.core.X
    (r'^(\s*)import core\.', r'\1import mozaiksai.core.'),
    # from core import X  →  from mozaiksai.core import X
    (r'^(\s*)from core import', r'\1from mozaiksai.core import'),
    # import core  →  import mozaiksai.core (rare but possible)
    (r'^(\s*)import core\s*$', r'\1import mozaiksai.core'),
]


def should_skip_dir(dir_path: Path) -> bool:
    """Check if directory should be skipped."""
    return any(skip in dir_path.parts for skip in SKIP_DIRS)


def find_python_files(root: Path) -> List[Path]:
    """Find all Python files in the repository."""
    python_files = []
    for path in root.rglob('*.py'):
        if not should_skip_dir(path):
            python_files.append(path)
    return python_files


def migrate_file(file_path: Path, dry_run: bool = False) -> Tuple[int, List[str]]:
    """
    Migrate a single file.
    
    Returns:
        Tuple of (number of changes, list of changed lines)
    """
    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"  ⚠️  Could not read {file_path}: {e}")
        return 0, []
    
    lines = content.split('\n')
    changes = []
    new_lines = []
    
    for i, line in enumerate(lines, 1):
        new_line = line
        for pattern, replacement in PATTERNS:
            if re.match(pattern, line, re.MULTILINE):
                new_line = re.sub(pattern, replacement, line)
                if new_line != line:
                    changes.append(f"  L{i}: {line.strip()}  →  {new_line.strip()}")
                break
        new_lines.append(new_line)
    
    if changes and not dry_run:
        new_content = '\n'.join(new_lines)
        file_path.write_text(new_content, encoding='utf-8')
    
    return len(changes), changes


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate core.* imports to mozaiksai.core.*')
    parser.add_argument('--dry-run', action='store_true', help='Show changes without applying')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show all changed lines')
    args = parser.parse_args()
    
    print("=" * 70)
    print("MozaiksAI Namespace Migration: core.* → mozaiksai.core.*")
    print("=" * 70)
    
    if args.dry_run:
        print("🔍 DRY RUN MODE - No files will be modified\n")
    
    python_files = find_python_files(REPO_ROOT)
    print(f"Found {len(python_files)} Python files to scan\n")
    
    total_changes = 0
    files_changed = 0
    
    for file_path in sorted(python_files):
        rel_path = file_path.relative_to(REPO_ROOT)
        num_changes, changes = migrate_file(file_path, dry_run=args.dry_run)
        
        if num_changes > 0:
            files_changed += 1
            total_changes += num_changes
            print(f"✏️  {rel_path} ({num_changes} changes)")
            if args.verbose:
                for change in changes:
                    print(change)
    
    print("\n" + "=" * 70)
    print(f"Summary: {total_changes} import statements in {files_changed} files")
    if args.dry_run:
        print("Run without --dry-run to apply changes")
    else:
        print("✅ Migration complete!")
    print("=" * 70)


if __name__ == '__main__':
    main()
