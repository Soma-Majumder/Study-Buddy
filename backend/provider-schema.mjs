// Claude's grammar supports shape constraints, but not our numeric/length
// bounds or array sizes of ten. Keep those in the original Python validator.
const supported = new Set(['type', 'properties', 'required', 'additionalProperties',
  'items', '$ref', '$defs', 'enum', 'const', 'pattern', 'description', 'anyOf']);
export function providerProjection(schema) {
  const result = {};
  for (const [key, value] of Object.entries(schema)) {
    if (!supported.has(key)) continue;
    if (key === 'properties' || key === '$defs') {
      result[key] = Object.fromEntries(Object.entries(value).map(([name, child]) => [name, providerProjection(child)]));
    } else if (key === 'items') result[key] = providerProjection(value);
    else if (key === 'anyOf') result[key] = value.map(providerProjection);
    else result[key] = structuredClone(value);
  }
  return result;
}
