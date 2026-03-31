/**
 * JokeRatingsCard Component
 * Displays joke ratings in a fun, visual format
 */

import React from 'react';

const styles = {
  card: {
    background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    borderRadius: '12px',
    padding: '20px',
    color: 'white',
    marginTop: '12px',
    marginBottom: '12px',
    boxShadow: '0 4px 15px rgba(0, 0, 0, 0.2)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    marginBottom: '16px',
  },
  title: {
    fontSize: '18px',
    fontWeight: '600',
    margin: 0,
  },
  ratingsList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
  },
  ratingItem: {
    background: 'rgba(255, 255, 255, 0.15)',
    borderRadius: '8px',
    padding: '12px',
  },
  ratingHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '4px',
  },
  jokeLabel: {
    fontWeight: '500',
    fontSize: '14px',
  },
  emoji: {
    fontSize: '18px',
  },
  feedback: {
    fontSize: '13px',
    opacity: 0.9,
  },
  summary: {
    marginTop: '16px',
    paddingTop: '16px',
    borderTop: '1px solid rgba(255, 255, 255, 0.2)',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  averageRating: {
    fontSize: '24px',
    fontWeight: '700',
  },
  verdict: {
    fontSize: '16px',
    fontWeight: '500',
  },
};

export default function JokeRatingsCard({ data }) {
  const { ratings = [], average_rating = 0, total_jokes = 0, verdict = '' } = data || {};

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <span style={{ fontSize: '24px' }}>🎬</span>
        <h3 style={styles.title}>Joke Review</h3>
      </div>

      <div style={styles.ratingsList}>
        {ratings.map((rating, index) => (
          <div key={index} style={styles.ratingItem}>
            <div style={styles.ratingHeader}>
              <span style={styles.jokeLabel}>Joke #{rating.joke_index + 1}</span>
              <span style={styles.emoji}>{rating.emoji_rating}</span>
            </div>
            <div style={styles.feedback}>{rating.feedback}</div>
          </div>
        ))}
      </div>

      <div style={styles.summary}>
        <div>
          <div style={{ fontSize: '12px', opacity: 0.8 }}>Average Rating</div>
          <div style={styles.averageRating}>{average_rating}/5</div>
        </div>
        <div style={styles.verdict}>{verdict}</div>
      </div>
    </div>
  );
}
