/**
 * Browser WebSocket credential transport.
 *
 * The browser WebSocket API cannot set request headers, so a browser cannot send
 * `Authorization: Bearer ...` on a WebSocket handshake. Putting the token in the URL
 * query string instead leaks it into access logs, browser history, `Referer`, and any
 * shared/bookmarked link — which is why the runtime rejects query-string tokens unless
 * `MOZAIKS_WS_ALLOW_QUERY_TOKEN` is explicitly enabled for local development.
 *
 * The credential is therefore carried in the `Sec-WebSocket-Protocol` handshake header,
 * the one request header `new WebSocket(url, protocols)` lets a browser populate. The
 * runtime reads the token from there, validates it through the configured auth adapter,
 * and echoes back only the marker value.
 *
 * Wire shape: [WS_BEARER_SUBPROTOCOL, base64url(access_token)]
 */

export const WS_BEARER_SUBPROTOCOL = 'mozaiks.bearer.v1';

/**
 * Encode a credential as unpadded base64url so any token shape stays a legal
 * handshake header token (RFC 7230), which raw credentials are not guaranteed to be.
 */
function encodeBearerCredential(token) {
  const bytes = new TextEncoder().encode(token);
  let binary = '';
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index]);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * Build the WebSocket subprotocol list carrying the access token.
 *
 * @param {string|null|undefined} token - Access token from the configured auth adapter.
 * @returns {string[]} Subprotocols to pass to `new WebSocket(url, protocols)`. Empty
 *   when there is no token, so unauthenticated local development still connects.
 */
export function buildWebSocketAuthProtocols(token) {
  if (typeof token !== 'string' || !token) {
    return [];
  }
  try {
    return [WS_BEARER_SUBPROTOCOL, encodeBearerCredential(token)];
  } catch {
    return [];
  }
}

/**
 * Open a WebSocket that carries the credential in the handshake header rather than
 * the URL. Never appends the token to `url`.
 *
 * @param {string} url - WebSocket URL, free of credentials.
 * @param {string|null|undefined} token - Access token, or null when auth is disabled.
 * @returns {WebSocket}
 */
export function openAuthenticatedWebSocket(url, token) {
  const protocols = buildWebSocketAuthProtocols(token);
  return protocols.length > 0 ? new WebSocket(url, protocols) : new WebSocket(url);
}
