/**
 * Build-time theming for Cloudscape components.
 *
 * Generates static CSS instead of runtime inline styles, eliminating
 * the need for 'unsafe-inline' in the Content Security Policy.
 *
 * Run before the main build: node scripts/build-theme.mjs
 * Output: frontend/themed/ (components + design-tokens with static CSS)
 */

import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { buildThemedComponents } from '@cloudscape-design/components-themeable/theming';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Default theme — no visual changes, just extracts styles to static CSS
const theme = {
  tokens: {},
};

console.log('Building themed Cloudscape components...');

await buildThemedComponents({
  theme,
  outputDir: join(__dirname, '../themed'),
});

console.log('Themed components generated in frontend/themed/');
