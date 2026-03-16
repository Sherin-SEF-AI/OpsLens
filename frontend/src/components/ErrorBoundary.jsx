import React from 'react';
import { AlertTriangle, RefreshCw, Home } from 'lucide-react';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });
    console.error('ErrorBoundary caught:', error, errorInfo);

    // Report to Sentry if configured
    if (typeof window !== 'undefined' && window.Sentry) {
      window.Sentry.captureException(error, { extra: errorInfo });
    }
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  handleGoHome = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    window.location.href = '/';
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    const isNetworkError =
      this.state.error?.message?.includes('fetch') ||
      this.state.error?.message?.includes('network') ||
      this.state.error?.message?.includes('Failed to fetch') ||
      this.state.error?.name === 'TypeError';

    return (
      <div className="flex items-center justify-center min-h-[400px] p-8" role="alert">
        <div className="text-center max-w-md">
          <div className="w-16 h-16 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center mx-auto mb-4">
            <AlertTriangle size={32} className="text-red-400" />
          </div>

          <h2 className="text-lg font-semibold text-dark-text mb-2">
            {isNetworkError ? 'Connection Error' : 'Something went wrong'}
          </h2>

          <p className="text-sm text-dark-muted mb-6">
            {isNetworkError
              ? 'Unable to connect to the server. Please check your connection and try again.'
              : 'An unexpected error occurred while rendering this page. You can try again or go back to the dashboard.'}
          </p>

          {this.state.error && (
            <details className="mb-6 text-left">
              <summary className="text-xs text-dark-muted cursor-pointer hover:text-dark-text">
                Technical details
              </summary>
              <pre className="mt-2 text-xs text-red-400/80 bg-dark-bg border border-dark-border rounded-lg p-3 overflow-auto max-h-32 font-mono">
                {this.state.error.toString()}
                {this.state.errorInfo?.componentStack && (
                  <span className="text-dark-muted">{this.state.errorInfo.componentStack}</span>
                )}
              </pre>
            </details>
          )}

          <div className="flex gap-3 justify-center">
            <button
              onClick={this.handleRetry}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
            >
              <RefreshCw size={14} />
              Try Again
            </button>
            <button
              onClick={this.handleGoHome}
              className="flex items-center gap-2 px-4 py-2 bg-dark-card border border-dark-border hover:bg-dark-border/50 rounded-lg text-sm font-medium text-dark-text transition-colors"
            >
              <Home size={14} />
              Dashboard
            </button>
          </div>
        </div>
      </div>
    );
  }
}
