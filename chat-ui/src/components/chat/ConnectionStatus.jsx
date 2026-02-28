import React from 'react';

/**
 * Connection Status Component
 * Shows real-time connection status with transport type and error handling
 */
function ConnectionStatus({ 
  isConnected, 
  status, 
  transportType, 
  error, 
  onRetry, 
  workflowName,
  className = '',
  onArtifactToggle
}) {
  const getStatusIcon = () => {
    switch (status) {
      case 'connected':
        return '🟢';
      case 'connecting':
      case 'reconnecting':
        return '🟡';
      case 'disconnected':
        return '🔴';
      case 'error':
        return '⚠️';
      default:
        return '⚪';
    }
  };

  const getStatusText = () => {
    switch (status) {
      case 'connected':
        return 'Connected';
      case 'connecting':
        return 'Connecting...';
      case 'reconnecting':
        return 'Reconnecting...';
      case 'disconnected':
        return 'Disconnected';
      case 'error':
        return 'Connection Error';
      default:
        return 'Unknown';
    }
  };

  const getTransportDisplay = () => {
    if (!transportType) return '';
    
    const transportDisplayMap = {
      websocket: 'WebSocket',
      http: 'HTTP Polling'
    };
    
    return transportDisplayMap[transportType] || transportType;
  };

  const showRetryButton = status === 'error' || status === 'disconnected';

  return (
    <div className={`connection-status ${status} ${className}`}>
      <div className="status-bar">
        <div className="status-main">
          <span className="status-icon">{getStatusIcon()}</span>
          <span className="status-text">{getStatusText()}</span>
          {transportType && (
            <span className="transport-type">via {getTransportDisplay()}</span>
          )}
        </div>

        {/* Mobile-only Artifact button inside status row */}
  {/* Artifact toggle is rendered separately in ChatInterface on mobile */}

        {showRetryButton && onRetry && (
          <button 
            onClick={onRetry}
            className="retry-button flex items-center justify-center ml-3"
            title="Retry connection"
            aria-label="Retry connection"
          >
            <span className="md:hidden" role="img" aria-label="Retry">🔄</span>
            <span className="hidden md:inline">🔄 Retry</span>
          </button>
        )}
      </div>

      {error && (
        <div className="error-details">
          <div className="error-message">
            <span className="error-icon">⚠️</span>
            <span className="error-text">{error.message}</span>
          </div>
          {error.stack && (typeof process !== 'undefined' && process.env && process.env.NODE_ENV === 'development') && (
            <details className="error-stack">
              <summary>Technical Details</summary>
              <pre>{error.stack}</pre>
            </details>
          )}
        </div>
      )}

      {/* Connection Quality Indicator */}
      {isConnected && (
        <div className="connection-quality">
          <div className="quality-bars">
            <div className="bar active"></div>
            <div className="bar active"></div>
            <div className="bar active"></div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ConnectionStatus;
