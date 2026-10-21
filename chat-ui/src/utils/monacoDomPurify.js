import createDOMPurify from 'dompurify';

// Monaco installs and removes hooks around each call. Keep those hooks separate
// from the shared sanitizer used by chat and Markdown primitives.
const purifier = createDOMPurify();

export default purifier;
