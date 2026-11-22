const deliberateCloseCodes = new Set([1000, 1001, 1005]);

// A local close or eviction by a newer socket must not reclaim the old session.
export function shouldReconnectAfterClose(closeInfo) {
  return !deliberateCloseCodes.has(closeInfo?.code);
}
