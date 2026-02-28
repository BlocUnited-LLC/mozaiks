#!/usr/bin/env python3
"""
Migrate workflow config files from JSON to YAML.

This script converts all workflow configuration files from .json to .yaml format.
It preserves all data structure and formatting while making configs more human-readable.
"""

import json
import yaml
from pathlib import Path
from typing import Dict, Any

# Config files to migrate
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


def convert_json_to_yaml(json_path: Path) -> Dict[str, Any]:
    """Load JSON file and return parsed data."""
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_yaml_file(yaml_path: Path, data: Dict[str, Any]) -> None:
    """Save data as YAML with clean formatting."""
    with open(yaml_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(
            data,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            indent=2
        )


def migrate_workflow(workflow_dir: Path, dry_run: bool = False) -> Dict[str, Any]:
    """
    Migrate all config files in a workflow directory from JSON to YAML.
    
    Returns:
        Dict with migration stats: {'converted': [...], 'skipped': [...], 'errors': [...]}
    """
    result = {
        'workflow': workflow_dir.name,
        'converted': [],
        'skipped': [],
        'errors': []
    }
    
    for config_name in CONFIG_FILES:
        json_path = workflow_dir / f"{config_name}.json"
        yaml_path = workflow_dir / f"{config_name}.yaml"
        
        # Skip if JSON doesn't exist
        if not json_path.exists():
            continue
        
        # Skip if YAML already exists (manual conversion or already migrated)
        if yaml_path.exists():
            result['skipped'].append(f"{config_name} (YAML exists)")
            continue
        
        try:
            # Load JSON
            data = convert_json_to_yaml(json_path)
            
            if dry_run:
                result['converted'].append(f"{config_name} (dry-run)")
            else:
                # Save as YAML
                save_yaml_file(yaml_path, data)
                result['converted'].append(config_name)
                print(f"  ✓ {config_name}.json → {config_name}.yaml")
        
        except Exception as e:
            result['errors'].append(f"{config_name}: {e}")
            print(f"  ✗ {config_name}.json - ERROR: {e}")
    
    return result


def main():
    """Migrate all workflows from JSON to YAML."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate workflow configs from JSON to YAML')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be migrated without making changes')
    parser.add_argument('--workflow', type=str, help='Migrate specific workflow only')
    args = parser.parse_args()
    
    workflows_dir = Path(__file__).parent.parent / 'workflows'
    
    if not workflows_dir.exists():
        print(f"❌ Workflows directory not found: {workflows_dir}")
        return 1
    
    print("=" * 60)
    print("JSON to YAML Migration Script")
    print("=" * 60)
    
    if args.dry_run:
        print("🔍 DRY RUN MODE - No files will be modified\n")
    
    # Get workflows to migrate
    if args.workflow:
        workflow_dirs = [workflows_dir / args.workflow]
        if not workflow_dirs[0].exists():
            print(f"❌ Workflow not found: {args.workflow}")
            return 1
    else:
        workflow_dirs = [d for d in workflows_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]
    
    # Migrate each workflow
    total_converted = 0
    total_skipped = 0
    total_errors = 0
    results = []
    
    for workflow_dir in sorted(workflow_dirs):
        # Skip internal/utility directories
        if workflow_dir.name in ['_shared', '__pycache__']:
            continue
        
        print(f"\n📁 {workflow_dir.name}/")
        result = migrate_workflow(workflow_dir, dry_run=args.dry_run)
        results.append(result)
        
        total_converted += len(result['converted'])
        total_skipped += len(result['skipped'])
        total_errors += len(result['errors'])
        
        if not result['converted'] and not result['errors']:
            print(f"  ⊘ No JSON files to migrate")
    
    # Summary
    print("\n" + "=" * 60)
    print("Migration Summary")
    print("=" * 60)
    print(f"Workflows processed: {len(results)}")
    print(f"Files converted: {total_converted}")
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
        print("\n💡 Run without --dry-run to perform actual migration")
    elif total_converted > 0:
        print("\n✅ Migration complete! Review the YAML files before removing JSON files.")
        print("   To remove old JSON files, run: python scripts/cleanup_json_files.py")
    
    return 0 if total_errors == 0 else 1


if __name__ == '__main__':
    exit(main())
