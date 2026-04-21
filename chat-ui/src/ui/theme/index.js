/**
 * @mozaiks/chat-ui — App UI theme system
 *
 * Exports the token generation and application functions used by themeProvider.js
 * to apply the --mz-* CSS variable set from a theme_config response.
 *
 * This module is separate from the existing chat-ui brand system (loadBrand.js /
 * BrandProvider) which handles --color-* and --core-primitive-* variables for
 * the chat interface. Both systems can coexist on the same page.
 */

export { generateThemeTokens, applyThemeTokens, getFontImports } from './tokens.js';
export { palettes } from './palettes.js';
export { fontFamilies, fontImports } from './fonts.js';
