import { useEffect, useState } from "react";
import { api } from "./api.js";

/*
 * The integration type specs (GET /api/integrations/types: label, description, fields, provides,
 * actions) are fetched once per page load and cached here. The manager builds its form from them and
 * the generic widgets use `provides` ("stats" / "items") to offer only suitable integrations.
 */
let cache = null;
let pending = null;

export function loadIntegrationTypes() {
  if (cache) return Promise.resolve(cache);
  pending ??= api
    .integrationTypes()
    .then((types) => (cache = types || []))
    .finally(() => {
      pending = null; // a failure is not cached: the next caller retries
    });
  return pending;
}

/** Test helper: forget the cached specs. */
export function resetIntegrationTypes() {
  cache = null;
  pending = null;
}

/** {types, error}: `types` is [] until loaded. `enabled=false` skips the request. */
export function useIntegrationTypes(enabled = true) {
  const [state, setState] = useState({ types: cache || [], error: "" });
  useEffect(() => {
    if (!enabled || cache) {
      if (cache) setState((s) => (s.types === cache ? s : { types: cache, error: "" }));
      return undefined;
    }
    let live = true;
    loadIntegrationTypes()
      .then((types) => live && setState({ types, error: "" }))
      .catch((e) => live && setState({ types: [], error: e.message }));
    return () => {
      live = false;
    };
  }, [enabled]);
  return state;
}

/** What a type provides ("stats", "items"); [] for an unknown type. */
export const provides = (types, type) => types.find((t) => t.type === type)?.provides || [];

/**
 * Integrations a widget field may pick: `integrationType` (one type or a list) and/or `capability`
 * ("stats" | "items", matched against the type specs).
 */
export function suitableIntegrations(integrations, types, { integrationType, capability } = {}) {
  const allowed = integrationType ? [].concat(integrationType) : null;
  return integrations.filter(
    (i) => (!allowed || allowed.includes(i.type)) && (!capability || provides(types, i.type).includes(capability))
  );
}
