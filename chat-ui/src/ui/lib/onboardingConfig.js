function stripInlineComment(value) {
  let result = '';
  let inSingle = false;
  let inDouble = false;

  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    const prev = index > 0 ? value[index - 1] : '';

    if (char === "'" && !inDouble && prev !== '\\') {
      inSingle = !inSingle;
      result += char;
      continue;
    }

    if (char === '"' && !inSingle && prev !== '\\') {
      inDouble = !inDouble;
      result += char;
      continue;
    }

    if (char === '#' && !inSingle && !inDouble) {
      break;
    }

    result += char;
  }

  return result.trimEnd();
}

function parseScalar(rawValue) {
  const value = stripInlineComment(rawValue).trim();
  if (!value) return '';

  const isQuoted =
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"));

  if (isQuoted && value.length >= 2) {
    const inner = value.slice(1, -1);
    return inner.replace(/\\(["'\\])/g, '$1');
  }

  if (value === 'true') return true;
  if (value === 'false') return false;
  if (value === 'null' || value === '~') return null;
  if (/^-?\d+(?:\.\d+)?$/.test(value)) return Number(value);

  return value;
}

export function parseOnboardingConfigSource(source) {
  if (typeof source !== 'string' || !source.trim()) return null;

  const config = {};
  const steps = [];
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  let currentStep = null;
  let inSteps = false;

  const commitStep = () => {
    if (!currentStep) return;
    const hasMeaningfulFields = Object.values(currentStep).some((value) => value !== '' && value !== null && value !== undefined);
    if (hasMeaningfulFields) {
      steps.push(currentStep);
    }
    currentStep = null;
  };

  for (const rawLine of lines) {
    const trimmedLine = rawLine.trim();
    if (!trimmedLine || trimmedLine.startsWith('#')) continue;

    if (trimmedLine === 'steps:' || trimmedLine.startsWith('steps:')) {
      commitStep();
      inSteps = true;
      continue;
    }

    if (!inSteps) {
      const match = trimmedLine.match(/^([A-Za-z0-9_]+):\s*(.*)$/);
      if (!match) continue;
      const [, key, rawValue] = match;
      config[key] = parseScalar(rawValue);
      continue;
    }

    if (trimmedLine.startsWith('- ')) {
      commitStep();
      currentStep = {};

      const inline = trimmedLine.slice(2).trim();
      if (inline) {
        const inlineMatch = inline.match(/^([A-Za-z0-9_]+):\s*(.*)$/);
        if (inlineMatch) {
          const [, key, rawValue] = inlineMatch;
          currentStep[key] = parseScalar(rawValue);
        }
      }
      continue;
    }

    if (!currentStep) continue;

    const match = trimmedLine.match(/^([A-Za-z0-9_]+):\s*(.*)$/);
    if (!match) continue;
    const [, key, rawValue] = match;
    currentStep[key] = parseScalar(rawValue);
  }

  commitStep();

  if (steps.length > 0) {
    config.steps = steps;
  }

  return Object.keys(config).length > 0 ? config : null;
}
