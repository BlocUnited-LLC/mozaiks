import { useState, useEffect, useRef, useCallback } from 'react';
import {
  TransitionChoicePanel,
  useTransitionMotion,
} from '@mozaiks/chat-ui/platform';

const REPO_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

function normalizeRepoInput(raw) {
  const trimmed = raw.trim();
  const match = trimmed.match(/(?:https?:\/\/)?github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)/);
  if (match) return match[1];
  return trimmed;
}

function useGithubOauthAvailable() {
  const [available, setAvailable] = useState(null);
  useEffect(() => {
    fetch('/api/oauth/github/status')
      .then((r) => r.json())
      .then((d) => setAvailable(Boolean(d?.configured)))
      .catch(() => setAvailable(false));
  }, []);
  return available;
}

function useGithubOauthPopup() {
  const popupRef = useRef(null);
  const pollRef = useRef(null);
  const [sessionKey, setSessionKey] = useState(null);
  const [status, setStatus] = useState('idle'); // idle | pending | connected | error

  const _stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  // Clean up on unmount
  useEffect(() => () => { _stopPolling(); }, [_stopPolling]);

  const open = useCallback(() => {
    const key = crypto.randomUUID();
    const url = `/api/oauth/github/connect?session_key=${encodeURIComponent(key)}`;
    const popup = window.open(url, 'github_oauth', 'width=600,height=700,scrollbars=yes,resizable=yes');
    if (!popup) { setStatus('error'); return; }
    popupRef.current = popup;
    setStatus('pending');

    // Poll /api/oauth/github/ready — works across any origin split (dev port 3000/8000 or prod same-origin)
    pollRef.current = setInterval(async () => {
      const popupClosed = !popupRef.current || popupRef.current.closed;
      try {
        const r = await fetch(`/api/oauth/github/ready?session_key=${encodeURIComponent(key)}`);
        const d = await r.json();
        if (d.ready) {
          _stopPolling();
          if (popupRef.current && !popupRef.current.closed) popupRef.current.close();
          popupRef.current = null;
          setSessionKey(key);
          setStatus('connected');
          return;
        }
      } catch (_) { /* network blip — keep polling */ }
      // If popup was closed by user before completing, stop
      if (popupClosed) {
        _stopPolling();
        setStatus((prev) => (prev === 'pending' ? 'idle' : prev));
        popupRef.current = null;
      }
    }, 1000);
  }, [_stopPolling]);

  const reset = useCallback(() => {
    _stopPolling();
    if (popupRef.current && !popupRef.current.closed) popupRef.current.close();
    popupRef.current = null;
    setSessionKey(null);
    setStatus('idle');
  }, [_stopPolling]);

  return { sessionKey, status, open, reset };
}

function useGithubRepos(sessionKey) {
  const [repos, setRepos] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!sessionKey) { setRepos(null); setError(null); return; }
    setLoading(true);
    setError(null);
    fetch(`/api/oauth/github/repos?session_key=${encodeURIComponent(sessionKey)}`)
      .then((r) => r.json())
      .then((d) => {
        if (d.repos) setRepos(d.repos);
        else setError(d.error || 'Could not load your repos.');
      })
      .catch(() => setError('Could not load your repos.'))
      .finally(() => setLoading(false));
  }, [sessionKey]);

  return { repos, loading, error };
}

export default function BrownfieldRepoInput({
  transition,
  onResolve,
  overlayTitleId,
  overlayDescriptionId,
}) {
  const option = Array.isArray(transition?.options) ? transition.options[0] : null;
  const motion = useTransitionMotion();
  const oauthAvailable = useGithubOauthAvailable();
  const { sessionKey, status: oauthStatus, open: openOAuth, reset: resetOAuth } = useGithubOauthPopup();
  const { repos, loading: reposLoading, error: reposError } = useGithubRepos(
    oauthStatus === 'connected' ? sessionKey : null
  );

  const [selectedRepo, setSelectedRepo] = useState('');
  const [repoSearch, setRepoSearch] = useState('');
  const [showManual, setShowManual] = useState(false);
  const [manualInput, setManualInput] = useState('');
  const [startMode, setStartMode] = useState(null);
  const [startError, setStartError] = useState(null);

  const isPickerMode = oauthStatus === 'connected';
  const normalizedManual = normalizeRepoInput(manualInput);
  const manualValid = !manualInput || REPO_RE.test(normalizedManual);
  const activeRepo = isPickerMode ? selectedRepo : normalizedManual;
  const canContinue = Boolean(activeRepo && (isPickerMode || manualValid));
  const isStarting = Boolean(startMode);

  const filteredRepos = repos
    ? repos.filter((r) => {
        if (!repoSearch) return true;
        const q = repoSearch.toLowerCase();
        return (
          r.full_name.toLowerCase().includes(q) ||
          (r.description && r.description.toLowerCase().includes(q))
        );
      })
    : [];

  const handleContinue = async () => {
    if (!option || !canContinue) return;
    const contextVariables = { github_repo: activeRepo };
    if (sessionKey) contextVariables.github_oauth_session = sessionKey;
    setStartError(null);
    setStartMode('repo');
    try {
      const result = await Promise.resolve(onResolve(option.id, contextVariables));
      if (result === false) {
        setStartMode(null);
        setStartError('Could not start repository discovery. Try again.');
      }
    } catch (_) {
      setStartMode(null);
      setStartError('Could not start repository discovery. Try again.');
    }
  };

  const handleSkip = async () => {
    if (!option) return;
    setStartError(null);
    setStartMode('manual');
    try {
      const result = await Promise.resolve(onResolve(option.id, {}));
      if (result === false) {
        setStartMode(null);
        setStartError('Could not start discovery. Try again.');
      }
    } catch (_) {
      setStartMode(null);
      setStartError('Could not start discovery. Try again.');
    }
  };

  const handleDisconnect = () => {
    resetOAuth();
    setSelectedRepo('');
    setRepoSearch('');
    setShowManual(false);
  };

  return (
    <TransitionChoicePanel
      eyebrow="Existing App"
      title="Choose the app to analyze"
      subtitle="Connect GitHub to pick the repo, or paste the URL below. Mozaiks reads this codebase before discovery starts."
      overlayTitleId={overlayTitleId}
      overlayDescriptionId={overlayDescriptionId}
      entered={motion.entered}
      prefersReducedMotion={motion.prefersReducedMotion}
    >
      <div className="mx-auto w-full max-w-3xl rounded-xl border border-border/60 bg-card/60 px-5 py-5 flex flex-col gap-4">

        {/* Connected state — repo picker */}
        {isPickerMode && (
          <>
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm font-semibold text-foreground">Choose a repository to analyze</p>
              <button
                type="button"
                onClick={handleDisconnect}
                className="shrink-0 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                Disconnect
              </button>
            </div>
            <input
              type="text"
              value={repoSearch}
              onChange={(e) => setRepoSearch(e.target.value)}
              placeholder="Search your repos..."
              className="w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/50"
            />
            <div className="max-h-56 overflow-y-auto rounded-lg border border-border/60 bg-background divide-y divide-border/40">
              {reposLoading && (
                <div className="px-4 py-6 text-center text-sm text-muted-foreground">Loading your repos...</div>
              )}
              {reposError && (
                <div className="px-4 py-4 text-center text-sm text-destructive">{reposError}</div>
              )}
              {!reposLoading && !reposError && filteredRepos.length === 0 && (
                <div className="px-4 py-6 text-center text-sm text-muted-foreground">
                  {repoSearch ? 'No repos match that search.' : 'No repos found.'}
                </div>
              )}
              {filteredRepos.map((repo) => {
                const isSelected = selectedRepo === repo.full_name;
                return (
                  <button
                    key={repo.full_name}
                    type="button"
                    onClick={() => setSelectedRepo(repo.full_name)}
                    className={[
                      'w-full text-left px-4 py-3 flex items-start gap-3 transition-colors',
                      isSelected ? 'bg-primary/10' : 'hover:bg-muted',
                    ].join(' ')}
                  >
                    <span className={['mt-0.5 shrink-0', isSelected ? 'text-primary' : 'text-muted-foreground'].join(' ')}>
                      {isSelected ? '\u25cf' : '\u25cb'}
                    </span>
                    <span className="flex-1 min-w-0">
                      <span className="block text-sm font-medium text-foreground truncate">{repo.full_name}</span>
                      {repo.description && (
                        <span className="block mt-0.5 text-xs text-muted-foreground truncate">{repo.description}</span>
                      )}
                    </span>
                    {repo.private && (
                      <span className="shrink-0 mt-0.5 rounded px-1.5 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground border border-border/60">
                        Private
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </>
        )}

        {/* Default state — connect button (or manual input) */}
        {!isPickerMode && (
          <>
            {oauthAvailable && !showManual && (
              <div className="flex flex-col items-center gap-3 py-2">
                <p className="text-sm text-muted-foreground text-center">
                  Connect your GitHub account to pick the repo you want Mozaiks to analyze.
                </p>
                <button
                  type="button"
                  onClick={openOAuth}
                  disabled={oauthStatus === 'pending'}
                  className="rounded-lg border border-border bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50"
                >
                  {oauthStatus === 'pending' ? 'Connecting...' : 'Connect GitHub'}
                </button>
                <button
                  type="button"
                  onClick={() => setShowManual(true)}
                  className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  or paste a URL instead
                </button>
              </div>
            )}

            {(!oauthAvailable || showManual) && (
              <>
                {showManual && (
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-semibold text-foreground">GitHub repository URL</p>
                    <button
                      type="button"
                      onClick={() => { setShowManual(false); setManualInput(''); }}
                      className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                      Back
                    </button>
                  </div>
                )}
                {!showManual && (
                  <p className="text-sm font-semibold text-foreground">GitHub repository to analyze</p>
                )}
                <input
                  type="text"
                  value={manualInput}
                  onChange={(e) => setManualInput(e.target.value)}
                  placeholder="acme-corp/my-app"
                  className="w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/50"
                />
                {!manualValid && (
                  <p className="text-xs text-destructive -mt-2">
                    Enter the username and repo name separated by a slash, like{' '}
                    <span className="font-mono">acme-corp/my-app</span>
                  </p>
                )}
              </>
            )}

            {oauthStatus === 'error' && (
              <p className="text-xs text-destructive text-center">
                GitHub connection failed. Try again or paste a URL instead.
              </p>
            )}
          </>
        )}
      </div>

      {option && (
        <div className="mx-auto w-full max-w-3xl flex flex-col gap-2">
          {startError && (
            <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {startError}
            </p>
          )}
          <button
            type="button"
            onClick={handleContinue}
            disabled={!canContinue || isStarting}
            className="w-full rounded-xl bg-primary px-6 py-3.5 text-sm font-semibold text-primary-foreground shadow-sm transition-opacity hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-primary/60 focus:ring-offset-2 focus:ring-offset-background disabled:opacity-50"
          >
            Scan &amp; Start Discovery
          </button>
          <button
            type="button"
            onClick={handleSkip}
            disabled={isStarting}
            className="w-full rounded-xl px-6 py-3 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus:outline-none"
          >
            Skip &mdash; I&apos;ll describe it manually
          </button>
        </div>
      )}
    </TransitionChoicePanel>
  );
}
