/**
 * Font definitions for the Mozaiks theme system.
 *
 * fontFamilies: CSS font-family strings keyed by font name.
 * fontImports: Google Fonts import URLs for hosted fonts (null for system fonts).
 *
 * Covers every font option from the theme config schema.
 * Internal to the theme engine — agents select fonts by name, never by CSS.
 */

export const fontFamilies = {
  system:       "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  // ── Mozaiks brand fonts (local — served from platform/brand/fonts/) ────────
  rajdhani:     "'Rajdhani', system-ui, sans-serif",
  orbitron:     "'Orbitron', 'Rajdhani', system-ui, sans-serif",
  oxanium:      "'Oxanium', 'Rajdhani', system-ui, sans-serif",
  // ── Google Fonts ────────────────────────────────────────────────────────────
  inter:        "'Inter', system-ui, sans-serif",
  roboto:       "'Roboto', system-ui, sans-serif",
  opensans:     "'Open Sans', system-ui, sans-serif",
  lato:         "'Lato', system-ui, sans-serif",
  poppins:      "'Poppins', system-ui, sans-serif",
  nunito:       "'Nunito', system-ui, sans-serif",
  montserrat:   "'Montserrat', system-ui, sans-serif",
  raleway:      "'Raleway', system-ui, sans-serif",
  playfair:     "'Playfair Display', Georgia, serif",
  merriweather: "'Merriweather', Georgia, serif",
  'source-code-pro': "'Source Code Pro', 'Courier New', monospace",
};

export const fontImports = {
  system:       null,
  // Brand fonts are loaded via @font-face from platform/brand/fonts/ — no Google Fonts fetch needed.
  rajdhani:     'https://fonts.googleapis.com/css2?family=Rajdhani:wght@300;400;500;600;700&display=swap',
  orbitron:     'https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;700;900&display=swap',
  oxanium:      'https://fonts.googleapis.com/css2?family=Oxanium:wght@300;400;500;600;700&display=swap',
  inter:        'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap',
  roboto:       'https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap',
  opensans:     'https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700&display=swap',
  lato:         'https://fonts.googleapis.com/css2?family=Lato:wght@400;700&display=swap',
  poppins:      'https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap',
  nunito:       'https://fonts.googleapis.com/css2?family=Nunito:wght@400;500;600;700&display=swap',
  montserrat:   'https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap',
  raleway:      'https://fonts.googleapis.com/css2?family=Raleway:wght@400;500;600;700&display=swap',
  playfair:     'https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&display=swap',
  merriweather: 'https://fonts.googleapis.com/css2?family=Merriweather:wght@400;700&display=swap',
  'source-code-pro': 'https://fonts.googleapis.com/css2?family=Source+Code+Pro:wght@400;500;600&display=swap',
};
