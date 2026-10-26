import {readFile} from 'node:fs/promises';

const canonical = JSON.parse(await readFile(new URL('../schemas/v1/quiz_output.schema.json', import.meta.url), 'utf8'));
import {providerProjection} from './provider-schema.mjs';

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
