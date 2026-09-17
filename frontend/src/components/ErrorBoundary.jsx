import { Component } from "react";

/**
 * Contains render/effect throws to the wrapped subtree.
 * Event-handler and async errors are NOT caught; those keep their own .catch.
 * While errored, the boundary resets when the `resetKey` reference changes.
 * It never remounts children on the happy path.
 */
export default class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidUpdate(prev) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (this.state.error) return this.props.fallback(this.state.error);
    return this.props.children;
  }
}
