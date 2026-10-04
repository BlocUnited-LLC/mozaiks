import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, ArrowUpRight, Heart, MessageCircle, RefreshCw, Sprout, Trash2 } from 'lucide-react';
import { Button, ErrorState, InlineEmptyState, LoadingState, Modal, SurfaceCard } from '@mozaiks/chat-ui/ui';
import { authorLabel, errorMessage, formatDate, postPath, socialAction } from '../lib/community.js';

const INPUT_CLASS = 'w-full resize-y rounded-lg border border-input bg-background px-4 py-3 text-base leading-7 text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60';
const INLINE_STATUS_CLASS = 'min-h-0 flex-none justify-start px-0 py-4';

function Author({ authorId, createdAt, user }) {
  const label = authorLabel(authorId, user);
  return (
    <div className="flex min-w-0 items-center gap-3">
      <span aria-hidden="true" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 font-semibold text-foreground">
        {label.slice(0, 1).toLocaleUpperCase()}
      </span>
      <div className="min-w-0">
        <p className="break-words text-sm font-semibold text-foreground">{label}</p>
        <time dateTime={createdAt} className="text-xs text-muted-foreground">{formatDate(createdAt)}</time>
      </div>
    </div>
  );
}

function DraftComposer({ kind, postId, onCreated }) {
  const isPost = kind === 'post';
  const limit = isPost ? 5000 : 2000;
  const fieldId = isPost ? 'community-post-body' : `community-comment-${postId}`;
  const [body, setBody] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);

  async function submit(event) {
    event.preventDefault();
    if (lock.current || !body.trim()) return;
    lock.current = true;
    setPending(true);
    setError('');
    try {
      const result = isPost
        ? await socialAction('create_post', { body: body.trim(), visibility: 'public' })
        : await socialAction('add_comment', { post_id: postId, body: body.trim() });
      const item = isPost ? result.post : result.comment;
      if (!item) throw new Error('The server did not confirm the saved message. Check the conversation before sending it again.');
      setBody('');
      onCreated(item);
    } catch (failure) {
      setError(errorMessage(failure, 'We could not confirm your message was saved. Your draft is still here. Refresh the conversation before sending it again.'));
    } finally {
      lock.current = false;
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3" aria-label={isPost ? 'Write a post' : 'Write a comment'}>
      <label htmlFor={fieldId} className="block text-sm font-semibold text-foreground">
        {isPost ? 'What would you like to share?' : 'Add to the conversation'}
      </label>
      <textarea
        id={fieldId} value={body} onChange={event => setBody(event.target.value)}
        rows={isPost ? 3 : 2} maxLength={limit} required disabled={pending}
        placeholder={isPost ? 'An idea, a question, or something worth passing along…' : 'Write a thoughtful reply…'}
        aria-describedby={`${fieldId}-help${error ? ` ${fieldId}-error` : ''}`} className={INPUT_CLASS}
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p id={`${fieldId}-help`} className="text-xs text-muted-foreground">
          {isPost ? 'Visible to everyone in this community.' : 'Keep it kind. Keep it constructive.'}
          <span className="ml-2 tabular-nums">{body.length.toLocaleString()}/{limit.toLocaleString()}</span>
        </p>
        <Button type="submit" disabled={pending || !body.trim()} className="min-h-11 px-5">
          {pending ? 'Sending…' : isPost ? 'Share post' : 'Post comment'}
          {!pending && <ArrowUpRight size={16} aria-hidden="true" />}
        </Button>
      </div>
      {error && <p id={`${fieldId}-error`} role="alert" className="text-sm text-destructive">{error}</p>}
    </form>
  );
}

function DeleteConfirmation({ target, onClose, onDeleted, onError, onPending }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  async function remove() {
    if (lock.current) return;
    lock.current = true;
    setPending(true);
    onPending(true);
    setError('');
    try {
      await socialAction(target.kind === 'post' ? 'delete_post' : 'delete_comment',
        target.kind === 'post' ? { post_id: target.id } : { comment_id: target.id });
      onDeleted(target);
    } catch (failure) {
      const message = errorMessage(failure, 'We could not confirm the deletion. Check the conversation before trying again.');
      setError(message);
      onError(message);
    } finally {
      lock.current = false;
      setPending(false);
      onPending(false);
    }
  }
  return (
    <Modal id="community-delete" open title={`Delete your ${target.kind}?`} size="small"
      description="This will remove it from the conversation. This action cannot be undone."
      onClose={onClose} error={error}>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="outline" disabled={pending} onClick={onClose} className="min-h-11">Keep it</Button>
        <Button type="button" variant="danger" disabled={pending} onClick={remove} className="min-h-11">
          {pending ? 'Deleting…' : `Delete ${target.kind}`}
        </Button>
      </div>
    </Modal>
  );
}

function ReactionButton({ postId, userId, revision }) {
  const [summary, setSummary] = useState(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  const mounted = useRef(true);
  const request = useRef(0);
  const load = useCallback(async () => {
    const version = ++request.current;
    setPending(true);
    setError('');
    try {
      const result = await socialAction('get_reaction_summary', { post_id: postId });
      if (!Number.isInteger(result.total) || result.total < 0 || !Object.hasOwn(result, 'viewer_reaction')) {
        throw new Error('Likes could not be loaded.');
      }
      if (mounted.current && version === request.current) setSummary(result);
    } catch (failure) {
      if (mounted.current && version === request.current) setError(errorMessage(failure, 'Likes could not be loaded.'));
    } finally {
      if (mounted.current && version === request.current) setPending(false);
    }
  }, [postId]);
  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; request.current += 1; };
  }, [load, userId, revision]);

  async function toggle() {
    if (lock.current || pending || !summary) return;
    lock.current = true;
    request.current += 1;
    setPending(true);
    setError('');
    try {
      const result = await socialAction('react_to_post', { post_id: postId, reaction_type: 'like' });
      if (!['added', 'removed'].includes(result.action) || !Number.isInteger(result.reaction_count) || result.reaction_count < 0) throw new Error('Your reaction was not confirmed. Reload likes to check it.');
      if (mounted.current) setSummary({ total: result.reaction_count, viewer_reaction: result.action === 'added' ? 'like' : null });
    } catch (failure) {
      if (mounted.current) {
        setSummary(null);
        setError(errorMessage(failure, 'Your reaction could not be confirmed. Reload likes before trying again.'));
      }
    } finally {
      lock.current = false;
      if (mounted.current) setPending(false);
    }
  }
  const liked = summary?.viewer_reaction === 'like';
  return (
    <div>
      {error ? (
        <div className="space-y-1">
          <p role="alert" className="text-xs text-destructive">{error}</p>
          <Button type="button" variant="ghost" onClick={load} disabled={pending} className="min-h-11">Reload likes</Button>
        </div>
      ) : (
        <Button type="button" variant="ghost" onClick={toggle} disabled={pending || !summary}
          aria-pressed={liked} aria-label={liked ? 'Unlike post' : 'Like post'} className="min-h-11 px-3">
          <Heart size={18} aria-hidden="true" fill={liked ? 'currentColor' : 'none'} />
          {summary ? <span>{summary.total} {summary.total === 1 ? 'like' : 'likes'}</span> : 'Loading likes…'}
        </Button>
      )}
    </div>
  );
}

function PostCard({ post, user, detail = false, onDelete, deleting, revision }) {
  return (
    <article aria-label={`Post by ${authorLabel(post.author_id, user)}`} data-post-id={post.post_id} data-testid={`post-${post.post_id}`}>
      <SurfaceCard className="bg-card">
        <div className="flex items-start justify-between gap-3">
          <Author authorId={post.author_id} createdAt={post.created_at} user={user} />
          {post.author_id === user?.id && (
            <Button type="button" variant="ghost" disabled={deleting} onClick={() => onDelete({ kind: 'post', id: post.post_id })}
              aria-label="Delete your post" className="min-h-11 min-w-11 px-3">
              <Trash2 size={16} aria-hidden="true" />
            </Button>
          )}
        </div>
        <p className="my-5 whitespace-pre-wrap break-words text-base leading-7 text-foreground [overflow-wrap:anywhere]">{post.body}</p>
        <div className="flex flex-wrap items-start gap-2 border-t border-border/60 pt-2">
          <ReactionButton postId={post.post_id} userId={user?.id} revision={revision} />
          {!detail && (
            <Button variant="ghost" asChild className="min-h-11 px-3">
              <Link to={postPath(post.post_id)} aria-label={`Open conversation, ${post.comment_count} comments`}>
                <MessageCircle size={18} aria-hidden="true" />
                {post.comment_count} {post.comment_count === 1 ? 'comment' : 'comments'}
              </Link>
            </Button>
          )}
        </div>
      </SurfaceCard>
    </article>
  );
}

function Comments({ postId, user, onDelete, revision, deleting }) {
  const [comments, setComments] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const request = useRef(0);
  const load = useCallback(async (after = null) => {
    const version = ++request.current;
    setPending(true);
    setError('');
    try {
      const result = await socialAction('list_comments', { post_id: postId, limit: 50, ...(after ? { after } : {}) });
      if (!Array.isArray(result.comments)) throw new Error('Comments could not be loaded.');
      if (version !== request.current) return;
      setComments(previous => after ? [...previous, ...result.comments.filter(item => !previous.some(old => old.comment_id === item.comment_id))] : result.comments);
      setCursor(result.next_cursor);
      setLoaded(true);
    } catch (failure) {
      if (version === request.current) setError(errorMessage(failure, 'Comments could not be loaded. Your draft is still here.'));
    } finally {
      if (version === request.current) setPending(false);
    }
  }, [postId]);
  useEffect(() => {
    void load();
    return () => { request.current += 1; };
  }, [load, revision]);
  return (
    <section aria-labelledby="conversation-heading" className="space-y-5">
      <h2 id="conversation-heading" className="text-xl font-semibold text-foreground">Conversation</h2>
      <SurfaceCard className="bg-card">
        <DraftComposer kind="comment" postId={postId} onCreated={() => { setAnnouncement('Your comment was posted.'); void load(); }} />
      </SurfaceCard>
      <p role="status" className="text-sm text-muted-foreground">{announcement}</p>
      {!loaded && pending && <LoadingState className={INLINE_STATUS_CLASS} label="Loading the conversation…" />}
      {error && <ErrorState className={INLINE_STATUS_CLASS} title="Could not load comments" message={error} action={{ label: 'Retry comments', onClick: () => load() }} />}
      {loaded && !error && comments.length === 0 && <InlineEmptyState title="There is room for your perspective." description="Be the first to leave a thoughtful reply." />}
      <div className="divide-y divide-border">
        {comments.map(comment => (
          <article key={comment.comment_id} data-comment-id={comment.comment_id} data-testid={`comment-${comment.comment_id}`} aria-label={`Comment by ${authorLabel(comment.author_id, user)}`} className="py-5 first:pt-0">
            <div className="flex items-start justify-between gap-3">
              <Author authorId={comment.author_id} createdAt={comment.created_at} user={user} />
              {comment.author_id === user?.id && <Button type="button" variant="ghost" disabled={deleting} onClick={() => onDelete({ kind: 'comment', id: comment.comment_id })} aria-label="Delete your comment" className="min-h-11 min-w-11 px-3"><Trash2 size={16} aria-hidden="true" /></Button>}
            </div>
            <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7 text-foreground [overflow-wrap:anywhere]">{comment.body}</p>
          </article>
        ))}
      </div>
      {cursor && <Button type="button" variant="outline" disabled={pending} onClick={() => load(cursor)} className="min-h-11">{pending ? 'Loading…' : 'More comments'}</Button>}
    </section>
  );
}

export function CommunityRoom({ user, postId = null }) {
  const navigate = useNavigate();
  const [posts, setPosts] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [commentRevision, setCommentRevision] = useState(0);
  const [feedRevision, setFeedRevision] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState('');
  const request = useRef(0);
  const load = useCallback(async (before = null) => {
    const version = ++request.current;
    setPending(true);
    setError('');
    try {
      const result = postId
        ? await socialAction('get_post', { post_id: postId })
        : await socialAction('list_posts', { visibility: 'public', limit: 20, ...(before ? { before } : {}) });
      if (postId ? !Object.hasOwn(result, 'post') : !Array.isArray(result.posts)) {
        throw new Error('The conversation could not be loaded.');
      }
      if (version !== request.current) return;
      const next = postId ? (result.post ? [result.post] : []) : result.posts;
      setPosts(previous => before ? [...previous, ...next.filter(item => !previous.some(old => old.post_id === item.post_id))] : next);
      setCursor(result.next_cursor || null);
      setLoaded(true);
      setFeedRevision(value => value + 1);
    } catch (failure) {
      if (version === request.current) setError(errorMessage(failure, 'We could not load the community. Please check your connection and try again.'));
    } finally {
      if (version === request.current) setPending(false);
    }
  }, [postId]);
  useEffect(() => {
    if (user?.id) void load();
    return () => { request.current += 1; };
  }, [load, user?.id]);

  function deleted(target) {
    setDeleteTarget(current => current?.id === target.id ? null : current);
    setActionError('');
    if (target.kind === 'post') {
      if (postId) { navigate('/community', { replace: true }); return; }
      setPosts(previous => previous.filter(post => post.post_id !== target.id));
      setAnnouncement('Your post was deleted.');
    } else {
      setCommentRevision(value => value + 1);
      setAnnouncement('Your comment was deleted.');
    }
  }

  if (!user?.id) return <LoadingState label="Opening your community…" />;

  return (
    <div className="mx-auto w-full max-w-content px-4 pb-24 pt-8 sm:px-6 sm:pt-12 lg:px-8">
      <header className="mb-8 border-b border-border/70 pb-8 sm:mb-10">
        {postId ? <Button variant="ghost" asChild className="mb-4 min-h-11 -ml-3"><Link to="/community"><ArrowLeft size={17} aria-hidden="true" />Back to community</Link></Button> : <p className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-muted-foreground"><Sprout size={17} aria-hidden="true" />Common Ground</p>}
        <h1 className="font-heading text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">{postId ? 'A good conversation starts here.' : 'The common room'}</h1>
        <p className="mt-3 max-w-2xl text-base leading-7 text-muted-foreground">{postId ? 'A little curiosity. A little kindness. A place for your perspective.' : 'Share what is on your mind. Find a fresh perspective. Make yourself at home.'}</p>
      </header>
      <div className="grid min-w-0 gap-8 lg:grid-cols-[minmax(0,2fr)_minmax(15rem,1fr)] lg:gap-12">
        <div className="min-w-0 space-y-6">
          {!postId && <SurfaceCard className="bg-card"><DraftComposer kind="post" onCreated={post => { request.current += 1; setPending(false); setPosts(previous => [post, ...previous.filter(item => item.post_id !== post.post_id)]); setLoaded(true); setAnnouncement('Your post was shared.'); }} /></SurfaceCard>}
          {!postId && <div className="flex items-center justify-between gap-3"><h2 className="text-sm font-semibold text-foreground">Latest conversations</h2><Button type="button" variant="ghost" onClick={() => load()} disabled={pending} aria-label="Refresh community" className="min-h-11"><RefreshCw size={15} aria-hidden="true" />Refresh</Button></div>}
          <p role="status" className="text-sm text-muted-foreground">{announcement}</p>
          {deleting && <p role="status" className="text-sm text-muted-foreground">Deleting your contribution…</p>}
          {actionError && <p role="alert" className="text-sm text-destructive">{actionError}</p>}
          {!loaded && pending && <LoadingState className={INLINE_STATUS_CLASS} label={postId ? 'Opening the conversation…' : 'Loading conversations…'} />}
          {error && <ErrorState className={INLINE_STATUS_CLASS} title="The community is unavailable" message={error} action={{ label: 'Retry loading', onClick: () => load() }} />}
          {loaded && !error && posts.length === 0 && <InlineEmptyState title={postId ? 'This post is no longer available.' : 'Every community starts with a hello.'} description={postId ? 'It may have been removed by its author. You can return to the latest conversations.' : 'Share an introduction, ask a question, or pass along something useful.'} action={postId ? { label: 'Back to community', to: '/community' } : null} />}
          <div className="space-y-4">
            {posts.map(post => <PostCard key={post.post_id} post={post} user={user} detail={Boolean(postId)} onDelete={setDeleteTarget} deleting={deleting} revision={feedRevision} />)}
          </div>
          {cursor && !postId && <Button type="button" variant="outline" disabled={pending} onClick={() => load(cursor)} className="min-h-11 w-full">{pending ? 'Loading…' : 'More conversations'}</Button>}
          {postId && posts.length > 0 && <Comments postId={postId} user={user} onDelete={setDeleteTarget} revision={commentRevision} deleting={deleting} />}
        </div>
        <aside aria-label="About this community" className="min-w-0 lg:sticky lg:top-28 lg:self-start">
          <SurfaceCard eyebrow="OUR SHARED SPACE" title="Good company. Open minds." className="bg-primary/5">
            <p className="text-sm leading-7 text-muted-foreground">There is no perfect thing to say. Bring a question, something you learned, or an idea still taking shape.</p>
            <ul className="mt-5 space-y-3 border-t border-border/70 pt-5 text-sm leading-6 text-foreground">
              <li>Be curious about other perspectives.</li>
              <li>Disagree with care and respect.</li>
              <li>Share only what you want the community to see.</li>
            </ul>
          </SurfaceCard>
          <p className="mt-4 px-1 text-xs leading-6 text-muted-foreground">Posts and comments are shared with everyone here. You can remove your own contributions at any time.</p>
        </aside>
      </div>
      {deleteTarget && <DeleteConfirmation key={`${deleteTarget.kind}:${deleteTarget.id}`} target={deleteTarget} onClose={() => setDeleteTarget(null)} onDeleted={deleted} onError={setActionError} onPending={setDeleting} />}
    </div>
  );
}
