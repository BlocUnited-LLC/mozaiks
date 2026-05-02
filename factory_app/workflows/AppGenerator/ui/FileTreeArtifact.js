// ==============================================================================
// FILE: ChatUI/src/workflows/AppGenerator/components/FileTreeArtifact.js
// DESCRIPTION: Generated file browser (tree) for AppWorkbench
// ==============================================================================

import React, { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, FileText, Folder, FolderOpen } from 'lucide-react';

const buildTree = (filesMap) => {
  const root = {};
  const paths = Object.keys(filesMap || {});
  for (const fullPath of paths) {
    const parts = String(fullPath).split('/').filter(Boolean);
    let cursor = root;
    for (let i = 0; i < parts.length; i += 1) {
      const part = parts[i];
      const isLeaf = i === parts.length - 1;
      if (!cursor[part]) {
        cursor[part] = isLeaf ? { __file: true, path: fullPath } : {};
      }
      cursor = cursor[part];
    }
  }
  return root;
};

const sortEntries = (entries, sortFoldersFirst = true) => {
  return [...entries].sort(([aName, aNode], [bName, bNode]) => {
    const aIsFile = Boolean(aNode && aNode.__file);
    const bIsFile = Boolean(bNode && bNode.__file);
    if (sortFoldersFirst && aIsFile !== bIsFile) return aIsFile ? 1 : -1;
    return String(aName).localeCompare(String(bName));
  });
};

const TreeNode = ({ name, node, depth, onSelectFile, selectedPath, defaultExpanded = [], sortFoldersFirst }) => {
  const isFile = Boolean(node && node.__file);
  const [open, setOpen] = useState(() => defaultExpanded.includes(name));
  const isSelected = isFile && node.path === selectedPath;

  const toggle = () => {
    if (isFile) {
      onSelectFile?.(node.path);
    } else {
      setOpen((v) => !v);
    }
  };

  return (
    <div>
      <button
        type="button"
        onClick={toggle}
        className={[
          'w-full flex items-center gap-2 rounded-md px-2 py-1 text-left transition-colors',
          isSelected
            ? 'bg-[rgba(var(--color-secondary-rgb),0.18)] text-[var(--color-secondary-light)]'
            : 'hover:bg-white/5 text-[var(--color-text-secondary)]',
        ].join(' ')}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
      >
        {!isFile ? (
          open ? <ChevronDown className="w-4 h-4 opacity-70" /> : <ChevronRight className="w-4 h-4 opacity-70" />
        ) : (
          <span className="w-4 h-4" />
        )}
        {!isFile ? (
          open ? (
            <FolderOpen className="w-4 h-4 text-[var(--color-accent)]" />
          ) : (
            <Folder className="w-4 h-4 text-[var(--color-accent)]" />
          )
        ) : (
          <FileText className="w-4 h-4 opacity-70" />
        )}
        <span className="text-sm truncate">{name}</span>
      </button>

      {!isFile && open && (
        <div>
          {sortEntries(Object.entries(node).filter(([k]) => k !== '__file'), sortFoldersFirst).map(([childName, childNode]) => (
            <TreeNode
              key={childName}
              name={childName}
              node={childNode}
              depth={depth + 1}
              onSelectFile={onSelectFile}
              selectedPath={selectedPath}
              defaultExpanded={defaultExpanded}
              sortFoldersFirst={sortFoldersFirst}
            />
          ))}
        </div>
      )}
    </div>
  );
};

const FileTreeArtifact = ({ filesMap = {}, config = {}, selectedPath, onSelectFile }) => {
  const treeConfig = useMemo(() => config?.artifacts?.['file-tree'] || {}, [config]);
  const tree = useMemo(() => buildTree(filesMap), [filesMap]);
  const entries = useMemo(() => sortEntries(Object.entries(tree), treeConfig.sortFoldersFirst !== false), [tree, treeConfig]);

  if (!filesMap || Object.keys(filesMap).length === 0) {
    return (
      <div className="rounded-xl border border-white/10 bg-black/30 p-4">
        <div className="text-sm text-[var(--color-text-muted)]">No generated files available.</div>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-white/10 bg-black/30 overflow-hidden">
      <div className="px-4 py-2 bg-black/40 border-b border-white/10">
        <div className="text-sm font-semibold text-white">Files</div>
        <div className="text-[10px] text-[var(--color-text-muted)]">{Object.keys(filesMap).length} files</div>
      </div>
      <div className="p-2 max-h-[520px] overflow-auto my-scroll1">
        {entries.map(([name, node]) => (
          <TreeNode
            key={name}
            name={name}
            node={node}
            depth={0}
            onSelectFile={onSelectFile}
            selectedPath={selectedPath}
            defaultExpanded={Array.isArray(treeConfig.defaultExpanded) ? treeConfig.defaultExpanded : []}
            sortFoldersFirst={treeConfig.sortFoldersFirst !== false}
          />
        ))}
      </div>
    </div>
  );
};

export default FileTreeArtifact;
