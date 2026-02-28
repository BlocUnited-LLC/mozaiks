#!/usr/bin/env python3
"""
Clean up old JSON config files after YAML migration.

This script removes the original .json config files after verifying
that corresponding .yaml files exist.
"""

from pathlib import Path
from typing import List, Dict, Any

# Config files that were migrated
CONFIG_FILES = [
    'orchestrator',
    'agents',
    'handoffs',
    'context_variables',
    'structured_outputs',
    'hooks',
    'tools',
    'ui_config'
]


def cleanup_workflow(workflow_dir: Path, dry_run: bool = False) -> Dict[str, Any]:
    """
    Remove JSON config files that have been migrated to YAML.
    
    Returns:
        Dict with cleanup stats: {'removed': [...], 'skipped': [...], 'errors': [...]}
    """
    result = {
        'workflow': workflow_dir.name,
        'removed': [],
        'skipped': [],
        'errors': []
    }
    
    for config_name in CONFIG_FILES:
        json_path = workflow_dir / f"{config_name}.json"
        yaml_path = workflow_dir / f"{config_name}.yaml"
        
        # Skip if JSON doesn't exist
        if not json_path.exists():
            continue
        
        # Only remove if corresponding YAML exists
        if not yaml_path.exists():
            result['skipped'].append(f"{config_name} (no YAML replacement)")
            print(f"  ⚠️  {config_name}.json - SKIPPED (no YAML found)")
            continue
        
        try:
            if dry_run:
                result['removed'].append(f"{config_name} (dry-run)")
                print(f"  🗑️  {config_name}.json (would be removed)")
            else:
                json_path.unlink()
                result['removed'].append(config_name)
                print(f"  ✓ {config_name}.json removed")
        
        except Exception as e:
            result['errors'].append(f"{config_name}: {e}")
            print(f"  ✗ {config_name}.json - ERROR: {e}")
    
    return result


def main():
    """Clean up JSON config files from all workflows."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Remove old JSON config files after YAML migration')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be removed without making changes')
    parser.add_argument('--workflow', type=str, help='Clean specific workflow only')
    args = parser.parse_args()
    
    workflows_dir = Path(__file__).parent.parent / 'workflows'
    
    if not workflows_dir.exists():
        print(f"❌ Workflows directory not found: {workflows_dir}")
        return 1
    
    print("=" * 60)
    print("JSON Config Cleanup Script")
    print("=" * 60)
    
    if args.dry_run:
        print("🔍 DRY RUN MODE - No files will be deleted\n")
    else:
        print("⚠️  This will permanently delete JSON config files!")
        response = input("Continue? (yes/no): ").strip().lower()
        if response != 'yes':
            print("Aborted.")
            return 0
        print()
    
    # Get workflows to clean
    if args.workflow:
        workflow_dirs = [workflows_dir / args.workflow]
        if not workflow_dirs[0].exists():
            print(f"❌ Workflow not found: {args.workflow}")
            return 1
    else:
        workflow_dirs = [d for d in workflows_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]
    
    # Clean each workflow
    total_removed = 0
    total_skipped = 0
    total_errors = 0
    results = []
    
    for workflow_dir in sorted(workflow_dirs):
        # Skip internal/utility directories
        if workflow_dir.name in ['_shared', '__pycache__']:
            continue
        
        print(f"\n📁 {workflow_dir.name}/")
        result = cleanup_workflow(workflow_dir, dry_run=args.dry_run)
        results.append(result)
        
        total_removed += len(result['removed'])
        total_skipped += len(result['skipped'])
        total_errors += len(result['errors'])
        
        if not result['removed'] and not result['skipped'] and not result['errors']:
            print(f"  ⊘ No JSON files to remove")
    
    # Summary
    print("\n" + "=" * 60)
    print("Cleanup Summary")
    print("=" * 60)
    print(f"Workflows processed: {len(results)}")
    print(f"Files removed: {total_removed}")
    print(f"Files skipped: {total_skipped}")
    print(f"Errors: {total_errors}")
    
    if total_errors > 0:
        print("\n⚠️  Errors encountered:")
        for result in results:
            if result['errors']:
                print(f"  {result['workflow']}:")
                for error in result['errors']:
                    print(f"    - {error}")
    
    if args.dry_run:
        print("\n💡 Run without --dry-run to perform actual cleanup")
    elif total_removed > 0:
        print("\n✅ Cleanup complete! Your workflows now use YAML exclusively.")
    
    return 0 if total_errors == 0 else 1


if __name__ == '__main__':
    exit(main())
