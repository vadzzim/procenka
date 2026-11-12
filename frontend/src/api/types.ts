import type { components } from "./schema";

// Aliases for generated schema types: no hand-written response shapes.
type Schemas = components["schemas"];

export type StatusOut = Schemas["StatusOut"];
export type RunOut = Schemas["RunOut"];
export type RunCreated = Schemas["RunCreated"];
export type SourceResultOut = Schemas["SourceResultOut"];
export type ProductsOut = Schemas["ProductsOut"];
export type ProductRowOut = Schemas["ProductRowOut"];
export type CalculationOut = Schemas["CalculationOut"];
export type OfferOut = Schemas["OfferOut"];
export type RejectedOfferOut = Schemas["RejectedOfferOut"];
export type DemoModesOut = Schemas["DemoModesOut"];
export type DemoResetOut = Schemas["DemoResetOut"];
export type SourceModeOut = Schemas["SourceModeOut"];
export type DemoMode = Schemas["DemoMode"];
export type Source = Schemas["Source"];
export type ResultState = Schemas["ResultState"];
export type ErrorOut = Schemas["ErrorOut"];
export type ConfirmationOut = Schemas["ConfirmationOut"];
export type ConfirmRefusalOut = Schemas["ConfirmRefusalOut"];
export type PublishedPriceOut = Schemas["PublishedPriceOut"];

// The selector needs the list of modes at runtime; the generated file has only types.
// `satisfies` plus the exhaustiveness check below make tsc fail when the schema
// gains, loses or renames a mode.
export const DEMO_MODES = [
  "normal",
  "markup_changed",
  "unavailable",
  "blocked",
  "price_spike",
] as const satisfies readonly DemoMode[];

type MissingModes = Exclude<DemoMode, (typeof DEMO_MODES)[number]>;
const allModesListed: [MissingModes] extends [never] ? true : MissingModes = true;
void allModesListed;
