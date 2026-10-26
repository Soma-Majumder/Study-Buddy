import {readFile} from 'node:fs/promises';

const canonical = JSON.parse(await readFile(new URL('../schemas/v1/quiz_output.schema.json', import.meta.url), 'utf8'));
// Claude's grammar supports shape constraints, but not our numeric/length
// bounds or array sizes of ten. Keep those in the original Python validator.
const supported = new Set(['type', 'properties', 'required', 'additionalProperties',
  'items', '$ref', '$defs', 'enum', 'const', 'pattern', 'description', 'anyOf']);
function providerProjection(schema) {
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
const success = providerProjection(canonical);
const definitions = success.$defs;
delete success.$defs;

// Both branches reject extra fields. Failure remains invalid as a quiz and is
// handled by the controller's existing repair/failure policy, never saved.
export const assessmentResponseSchema = {
  type: 'object', additionalProperties: false, required: ['result'],
  $defs: definitions,
  properties: {result: {anyOf: [success, {
    type: 'object', additionalProperties: false, required: ['error'],
    properties: {error: {
      type: 'object', additionalProperties: false, required: ['code', 'message'],
      properties: {code: {const: 'insufficient_quiz_content'}, message: {type: 'string'}},
    }},
  }]}},
};
