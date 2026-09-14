/**
 * RepoAccessRecoveryCard - visible recovery state for private GitHub imports.
 *
 * The backend emits this when a repo URL was supplied but GitHub would not
 * return repository or tree data with the available credentials.
 */

import {
  AlertTriangle,
  ExternalLink,
  FolderGit2,
  KeyRound,
  RefreshCw,
} from 'lucide-react';
import { Button, StatusPill } from '@mozaiks/chat-ui/ui';

const EMPTY_ARRAY = Object.freeze([]);

function asArray(value) {
  return Array.isArray(value) ? value : EMPTY_ARRAY;
}

function labelize(value) {
  return String(value || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function ActionIcon({ kind }) {
  if (kind === 'oauth_retry') return <KeyRound className="h-4 w-4" aria-hidden="true" />;
  if (kind === 'local_path') return <FolderGit2 className="h-4 w-4" aria-hidden="true" />;
  return <RefreshCw className="h-4 w-4" aria-hidden="true" />;
}

export default function RepoAccessRecoveryCard({ payload = {} }) {
  const repo = payload.github_repo || 'GitHub repository';
  const githubUrl = payload.github_url || (payload.github_repo ? `https://github.com/${payload.github_repo}` : '');
  const actions = asArray(payload.recovery_actions);
  const progress = payload.app_intelligence_progress || {};
  const httpStatus = payload.http_status ? `HTTP ${payload.http_status}` : null;

  const startImportAgain = () => {
    window.location.assign('/create');
  };

  return (
    <div className="w-full overflow-hidden rounded-lg border border-warning/35 bg-card text-card-foreground shadow-sm">
      <div className="border-b border-warning/25 bg-warning/10 px-4 py-3">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-warning">
                <FolderGit2 className="h-3.5 w-3.5" aria-hidden="true" />
                Repository Access Needed
              </span>
              {httpStatus && <StatusPill tone="warning" label={httpStatus} />}
            </div>
            <p className="mt-1 truncate text-sm font-semibold text-foreground">{repo}</p>
            {githubUrl && (
              <a
                href={githubUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-0.5 inline-flex max-w-full items-center gap-1 font-mono text-[11px] text-muted-foreground hover:text-foreground"
              >
                <span className="truncate">{githubUrl}</span>
                <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
              </a>
            )}
          </div>
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-warning" aria-label="Repository access needed" />
        </div>
      </div>

      <div className="space-y-3 px-4 py-3">
        <p className="text-xs leading-5 text-foreground/85">
          {payload.message || 'Mozaiks needs GitHub read access before it can index this repository.'}
        </p>

        {progress.message && (
          <div className="rounded-md border border-border/55 bg-background/70 px-3 py-2">
            <p className="text-[11px] font-medium text-muted-foreground">Indexing stopped</p>
            <p className="mt-1 text-xs text-foreground">{progress.message}</p>
          </div>
        )}

        {actions.length > 0 && (
          <div className="grid gap-2">
            {actions.slice(0, 3).map((action) => (
              <div
                key={action.id || action.label}
                className="flex min-w-0 items-start gap-2 rounded-md border border-border/50 bg-background/65 px-3 py-2"
              >
                <span className="mt-0.5 shrink-0 text-muted-foreground">
                  <ActionIcon kind={action.kind} />
                </span>
                <span className="min-w-0">
                  <span className="block text-xs font-medium text-foreground">{action.label || labelize(action.kind)}</span>
                  {action.description && (
                    <span className="mt-0.5 block text-[11px] leading-5 text-muted-foreground">
                      {action.description}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-col gap-2 sm:flex-row">
          <Button
            variant="primary"
            size="sm"
            onClick={startImportAgain}
            icon={<RefreshCw className="h-4 w-4" aria-hidden="true" />}
            label="Start Import Again"
          />
          {githubUrl && (
            <Button asChild variant="outline" size="sm">
              <a href={githubUrl} target="_blank" rel="noreferrer">
                <ExternalLink className="h-4 w-4" aria-hidden="true" />
                Open Repo
              </a>
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
