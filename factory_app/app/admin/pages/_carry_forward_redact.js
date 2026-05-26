// Sensitive-term redaction helpers shared by CarryForwardReportPanel and
// CarryForwardReportSummary. The resolver already filters these out at
// generation time; these helpers are belt-and-suspenders for display safety.

export const _SENSITIVE_TERMS = ['secret', 'credential', 'password']

export function _isSensitivePath(path) {
  const lower = String(path || '').toLowerCase()
  return _SENSITIVE_TERMS.some((term) => lower.includes(term))
}

export function _sanitizePaths(paths) {
  return Array.isArray(paths)
    ? paths.map((p) => (_isSensitivePath(p) ? '[redacted]' : p))
    : []
}
