import { Component } from 'react';

/**
 * Error Boundary component for graceful error handling.
 * Catches JavaScript errors in child components and displays a fallback UI.
 *
 * Usage:
 *   <ErrorBoundary fallback={<ErrorFallback />}>
 *     <YourComponent />
 *   </ErrorBoundary>
 */
class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });

    // Log to console in development
    if (process.env.NODE_ENV === 'development') {
      console.error('[ErrorBoundary] Caught error:', error);
      console.error('[ErrorBoundary] Component stack:', errorInfo?.componentStack);
    }

    // Call optional onError callback
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    }
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    if (this.state.hasError) {
      // Use custom fallback if provided
      if (this.props.fallback) {
        return this.props.fallback;
      }

      // Default fallback UI
      return (
        <div className="flex flex-col items-center justify-center min-h-[200px] p-6 bg-[rgba(15,23,42,0.9)] rounded-2xl border border-red-500/30">
          <div className="text-red-400 text-lg font-semibold mb-2">
            Something went wrong
          </div>
          <p className="text-slate-400 text-sm text-center mb-4 max-w-md">
            {this.state.error?.message || 'An unexpected error occurred'}
          </p>
          <button
            onClick={this.handleRetry}
            className="px-4 py-2 bg-red-500/20 hover:bg-red-500/30 text-red-300 rounded-lg border border-red-500/40 transition-colors text-sm font-medium"
          >
            Try Again
          </button>
          {process.env.NODE_ENV === 'development' && this.state.errorInfo && (
            <details className="mt-4 text-xs text-slate-500 max-w-full overflow-auto">
              <summary className="cursor-pointer hover:text-slate-400">Stack trace</summary>
              <pre className="mt-2 p-2 bg-slate-900 rounded text-left whitespace-pre-wrap">
                {this.state.errorInfo.componentStack}
              </pre>
            </details>
          )}
        </div>
      );
    }

    return this.props.children;
  }
}

/**
 * Lightweight error fallback for chat messages
 */
export const ChatMessageErrorFallback = () => (
  <div className="px-4 py-2 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
    Failed to render message
  </div>
);

/**
 * Error fallback for chat interface
 */
export const ChatInterfaceErrorFallback = ({ onRetry }) => (
  <div className="flex flex-col items-center justify-center h-full p-8 text-center bg-[rgba(15,23,42,0.95)]">
    <div className="text-red-400 text-5xl mb-4">💬</div>
    <h3 className="text-white text-lg font-semibold mb-2">Chat Error</h3>
    <p className="text-slate-400 text-sm mb-4 max-w-md">
      The chat interface encountered an error. Your messages are safe.
    </p>
    {onRetry && (
      <button
        onClick={onRetry}
        className="px-4 py-2 bg-blue-500/20 hover:bg-blue-500/30 text-blue-300 rounded-lg border border-blue-500/40 transition-colors text-sm"
      >
        Reload Chat
      </button>
    )}
  </div>
);

/**
 * Error fallback for artifact panel
 */
export const ArtifactErrorFallback = ({ onRetry }) => (
  <div className="flex flex-col items-center justify-center h-full p-8 text-center">
    <div className="text-amber-400 text-5xl mb-4">⚠️</div>
    <h3 className="text-white text-lg font-semibold mb-2">Artifact Error</h3>
    <p className="text-slate-400 text-sm mb-4">
      Unable to display this artifact
    </p>
    {onRetry && (
      <button
        onClick={onRetry}
        className="px-4 py-2 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 rounded-lg border border-amber-500/40 transition-colors text-sm"
      >
        Retry
      </button>
    )}
  </div>
);

export default ErrorBoundary;
