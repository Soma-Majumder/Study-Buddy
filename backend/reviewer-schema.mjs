import {readFile} from 'node:fs/promises';
import Ajv2020 from 'ajv/dist/2020.js';
import {providerProjection} from './provider-schema.mjs';

const canonical = JSON.parse(await readFile(new URL('../schemas/v1/quiz_quality_review.schema.json', import.meta.url), 'utf8'));
// Provider grammar is only a first boundary; retain all canonical constraints locally.
export const reviewerResponseSchema = providerProjection(canonical);
export const matchesReviewerSchema = new Ajv2020({allErrors: true}).compile(canonical);
