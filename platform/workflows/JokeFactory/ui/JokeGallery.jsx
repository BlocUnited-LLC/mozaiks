/**
 * JokeGallery Component (Artifact)
 * Displays all generated jokes in a gallery format in the artifact panel
 */

import React, { useState } from 'react';

const styles = {
  container: {
    fontFamily: 'system-ui, -apple-system, sans-serif',
    background: '#0f0f1a',
    minHeight: '100%',
    padding: '24px',
    color: '#eee',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    marginBottom: '24px',
    paddingBottom: '16px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
  },
  title: {
    fontSize: '24px',
    fontWeight: '600',
    margin: 0,
  },
  subtitle: {
    fontSize: '14px',
    color: '#888',
    marginTop: '4px',
  },
  statsBar: {
    display: 'flex',
    gap: '16px',
    marginBottom: '24px',
  },
  statCard: {
    background: 'rgba(99, 102, 241, 0.15)',
    borderRadius: '12px',
    padding: '16px 24px',
    flex: 1,
    textAlign: 'center',
  },
  statValue: {
    fontSize: '28px',
    fontWeight: '700',
    color: '#6366f1',
  },
  statLabel: {
    fontSize: '12px',
    color: '#888',
    marginTop: '4px',
  },
  jokesList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '16px',
  },
  jokeCard: {
    background: 'linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)',
    borderRadius: '16px',
    padding: '24px',
    border: '1px solid rgba(255, 255, 255, 0.05)',
    transition: 'transform 0.2s, box-shadow 0.2s',
    cursor: 'pointer',
  },
  jokeCardHover: {
    transform: 'translateY(-2px)',
    boxShadow: '0 8px 24px rgba(0, 0, 0, 0.3)',
  },
  jokeHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '16px',
  },
  jokeNumber: {
    background: '#6366f1',
    borderRadius: '20px',
    padding: '4px 12px',
    fontSize: '12px',
    fontWeight: '600',
  },
  jokeStyle: {
    fontSize: '12px',
    color: '#888',
    textTransform: 'uppercase',
    letterSpacing: '1px',
  },
  jokeText: {
    fontSize: '18px',
    lineHeight: 1.6,
    marginBottom: '16px',
  },
  jokeFooter: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingTop: '16px',
    borderTop: '1px solid rgba(255, 255, 255, 0.05)',
  },
  topicTag: {
    background: 'rgba(255, 255, 255, 0.1)',
    borderRadius: '6px',
    padding: '4px 10px',
    fontSize: '12px',
  },
  ratingBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
  },
  copyButton: {
    background: 'transparent',
    border: '1px solid rgba(255, 255, 255, 0.2)',
    borderRadius: '8px',
    padding: '8px 16px',
    color: '#eee',
    cursor: 'pointer',
    fontSize: '12px',
    transition: 'all 0.2s',
  },
  emptyState: {
    textAlign: 'center',
    padding: '60px 20px',
    color: '#666',
  },
  emptyIcon: {
    fontSize: '48px',
    marginBottom: '16px',
  },
};

export default function JokeGallery({ data }) {
  const [hoveredIndex, setHoveredIndex] = useState(null);
  const [copiedIndex, setCopiedIndex] = useState(null);

  const { jokes = [], ratings = [], session_stats = {} } = data || {};

  const copyToClipboard = (text, index) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  const getRatingForJoke = (index) => {
    const rating = ratings.find(r => r.joke_index === index);
    return rating ? rating.emoji_rating : null;
  };

  if (!jokes.length) {
    return (
      <div style={styles.container}>
        <div style={styles.emptyState}>
          <div style={styles.emptyIcon}>🎭</div>
          <h3>No jokes yet!</h3>
          <p>Start chatting to generate some laughs</p>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={{ fontSize: '32px' }}>🎭</span>
        <div>
          <h1 style={styles.title}>Joke Gallery</h1>
          <div style={styles.subtitle}>Your collection of comedy gold</div>
        </div>
      </div>

      <div style={styles.statsBar}>
        <div style={styles.statCard}>
          <div style={styles.statValue}>{jokes.length}</div>
          <div style={styles.statLabel}>Total Jokes</div>
        </div>
        <div style={styles.statCard}>
          <div style={styles.statValue}>{session_stats.average_rating || '—'}</div>
          <div style={styles.statLabel}>Avg Rating</div>
        </div>
        <div style={styles.statCard}>
          <div style={styles.statValue}>{session_stats.top_style || '—'}</div>
          <div style={styles.statLabel}>Top Style</div>
        </div>
      </div>

      <div style={styles.jokesList}>
        {jokes.map((joke, index) => (
          <div
            key={index}
            style={{
              ...styles.jokeCard,
              ...(hoveredIndex === index ? styles.jokeCardHover : {}),
            }}
            onMouseEnter={() => setHoveredIndex(index)}
            onMouseLeave={() => setHoveredIndex(null)}
          >
            <div style={styles.jokeHeader}>
              <span style={styles.jokeNumber}>#{index + 1}</span>
              <span style={styles.jokeStyle}>{joke.style || 'General'}</span>
            </div>

            <div style={styles.jokeText}>{joke.text}</div>

            <div style={styles.jokeFooter}>
              <span style={styles.topicTag}>📌 {joke.topic || 'General'}</span>

              <div style={styles.ratingBadge}>
                {getRatingForJoke(index) && (
                  <span>{getRatingForJoke(index)}</span>
                )}
                <button
                  style={styles.copyButton}
                  onClick={() => copyToClipboard(joke.text, index)}
                >
                  {copiedIndex === index ? '✓ Copied!' : '📋 Copy'}
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
