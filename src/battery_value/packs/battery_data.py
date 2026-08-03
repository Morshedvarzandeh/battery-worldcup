"""Read pack and vehicle data from the battery-data database.

battery-data (github.com/Morshedvarzandeh/battery-data) is the source of
truth for what a battery *is*: which pack is fielded in which vehicle, what it
is assembled from, what it is made of, and with what evidence. Keeping a second
copy of that here would make two answers to the same question, so this module
reads it rather than restating it.

Three layers, tried in order, mirroring the price provider chain:

1. **Postgres** when ``BV_BATTERY_DATA_DSN`` is set. Richest, and sees the
   attribution columns, so a claim can be filtered on how well evidenced it is.
2. **HTTP** when ``BV_BATTERY_DATA_URL`` is set. No database coupling.
3. **The bundled snapshot**, which is a generated cache of the above rather
   than a hand-maintained list. That is what keeps battery-value working
   offline on a fresh clone with nothing configured.

The attribution filter matters. battery-data deliberately stores weakly
evidenced claims -- forum consensus, inference from form factor -- rather than
discarding them, with a ``basis`` and a ``confidence`` saying so. Valuing a
pack against a guess would launder that guess into a number with a currency
symbol, so the default here only trusts claims at or above
:data:`MINIMUM_ATTRIBUTION_CONFIDENCE`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from ..passport.models import BatteryPassport
from .catalogue import catalogue_from_documents
from .models import PackMatch
from .providers import PackDataProvider

logger = logging.getLogger(__name__)

ENV_DSN = "BV_BATTERY_DATA_DSN"
ENV_URL = "BV_BATTERY_DATA_URL"

# Below this, battery-data is telling us the link is a guess. A residual value
# built on a guessed pack identity is worse than admitting the pack is unknown,
# because the number looks equally confident either way.
MINIMUM_ATTRIBUTION_CONFIDENCE = 0.5

# Attribution bases that are somebody's opinion rather than an observation.
WEAK_BASES = frozenset({"community_reported", "inferred"})

# One query, because the interesting part is the join: pack, its assembly,
# its chemistry, the vehicles it is fielded in, and what its parts fetch.
# Components with a used market, keyed by the pack they belong to. Kept as a
# second query because a pack has a variable number of them and folding that
# into the pack row would multiply it out.
_COMPONENT_QUERY = """
SELECT parent.uid AS pack_uid,
       child.uid  AS component_uid,
       child.kind::text AS kind,
       asm.quantity,
       cmv.unit_value
  FROM bd.product_assembly asm
  JOIN bd.product_revision pr   ON pr.id = asm.parent_revision_id
  JOIN bd.product parent        ON parent.id = pr.product_id
  JOIN bd.product_revision cr   ON cr.id = asm.child_revision_id
  JOIN bd.product child         ON child.id = cr.product_id
  JOIN bd.component_market_value cmv
       ON cmv.product_revision_id = cr.id AND cmv.valid_to IS NULL
 WHERE parent.kind = 'pack' AND child.kind <> 'module'
"""

_PACK_QUERY = """
SELECT
  p.uid                              AS product_uid,
  p.model_number,
  o.name                             AS organisation,
  p.brand                            AS brand,
  pc.designation                     AS chemistry,
  p.form_factor_code,
  r.id                               AS revision_id,
  asm.quantity                       AS module_count,
  energy.value_si / 3.6e6            AS rated_kwh,
  mass.value_si                      AS pack_mass_kg,
  cmv.unit_value                     AS used_module_value_eur,
  cmv.sell_through                   AS sell_through,
  rp.price_per_kwh                   AS oem_replacement_price_eur_per_kwh,
  array_remove(array_agg(DISTINCT a.name), NULL)      AS vehicle_models,
  array_remove(array_agg(DISTINCT al.alias), NULL)    AS aliases,
  max(pa.confidence)                 AS attribution_confidence,
  min(pa.basis::text)                AS attribution_basis
FROM bd.product p
JOIN bd.organization o          ON o.id = p.manufacturer_id
JOIN bd.product_revision r      ON r.product_id = p.id
-- Modules only: a pack also assembles a BMS and an HV box, and letting
-- those through would make module_count whichever child came first.
LEFT JOIN bd.product_assembly asm ON asm.parent_revision_id = r.id
     AND EXISTS (SELECT 1 FROM bd.product_revision x
                   JOIN bd.product xp ON xp.id = x.product_id
                  WHERE x.id = asm.child_revision_id AND xp.kind = 'module')
LEFT JOIN bd.product_chemistry pc ON pc.product_revision_id = r.id
LEFT JOIN bd.product_revision mr  ON mr.id = asm.child_revision_id
LEFT JOIN bd.product mp           ON mp.id = mr.product_id
LEFT JOIN bd.component_market_value cmv
       ON cmv.product_revision_id = mr.id AND cmv.valid_to IS NULL
LEFT JOIN bd.replacement_price rp
       ON rp.product_revision_id = r.id AND rp.valid_to IS NULL
LEFT JOIN bd.product_application pa
       ON pa.product_revision_id = r.id AND pa.superseded_by IS NULL
LEFT JOIN bd.application a      ON a.id = pa.application_id
LEFT JOIN bd.product_alias al   ON al.product_id = p.id
-- Energy and mass are observations, not columns. Taking the nominal one
-- keeps this to the nameplate figure rather than a test result.
LEFT JOIN bd.observation energy
       ON energy.product_revision_id = r.id
      AND energy.statistic = 'nominal'
      AND energy.quantity_id = (SELECT id FROM bd.quantity WHERE code='energy')
LEFT JOIN bd.observation mass
       ON mass.product_revision_id = r.id
      AND mass.statistic = 'nominal'
      AND mass.quantity_id = (SELECT id FROM bd.quantity WHERE code='mass')
WHERE p.kind = 'pack'
GROUP BY p.uid, p.model_number, o.name, p.brand, pc.designation,
         p.form_factor_code,
         r.id, asm.quantity, cmv.unit_value, cmv.sell_through,
         rp.price_per_kwh, energy.value_si, mass.value_si
"""


def _row_to_document(row: dict[str, Any]) -> dict[str, Any] | None:
    """Turn a battery-data row into a catalogue model document.

    Returns ``None`` when the row lacks what a valuation needs, rather than
    inventing a default that would look like knowledge.
    """
    chemistry = row.get("chemistry")
    if not chemistry:
        return None

    confidence = row.get("attribution_confidence")
    basis = row.get("attribution_basis")
    if confidence is not None and float(confidence) < MINIMUM_ATTRIBUTION_CONFIDENCE:
        return None
    if basis in WEAK_BASES:
        return None

    # battery-data uids look like 'pack/nissan/nissan-leaf-ze1-40'; the
    # catalogue key is the last segment. The SQL layer aliases the column to
    # 'key' and the HTTP API calls it 'product_uid', so accept either rather
    # than making one of the two paths a special case.
    uid = row.get("key") or row.get("product_uid")
    if not uid:
        return None
    key = str(uid).rsplit("/", 1)[-1]

    document = {
        "key": key,
        "label": row.get("model_number") or key,
        # battery-data stores the legal entity ("Nissan Motor"); a passport
        # names the brand ("Nissan"). Matching wants the brand.
        "manufacturer": row.get("brand") or row.get("manufacturer")
        or row.get("organisation"),
        "chemistry": chemistry,
        "vehicle_models": list(row.get("vehicle_models") or ()),
        "aliases": list(row.get("aliases") or ()),
        "module_count": row.get("module_count"),
        "cell_format": row.get("form_factor_code"),
        "used_module_value_eur": float(row.get("used_module_value_eur") or 0.0),
        "source": "battery-data",
    }

    # sell_through is battery-data's name for how readily this model's
    # hardware finds a buyer, which is what the catalogue calls demand.
    sell_through = row.get("sell_through")
    if sell_through is not None:
        document["second_life_demand"] = (
            "high" if float(sell_through) >= 0.92
            else "medium" if float(sell_through) >= 0.78
            else "low"
        )

    if row.get("oem_replacement_price_eur_per_kwh") is not None:
        document["oem_replacement_price_eur_per_kwh"] = float(
            row["oem_replacement_price_eur_per_kwh"]
        )
    if confidence is not None:
        document["confidence"] = (
            "high" if float(confidence) >= 0.8
            else "medium" if float(confidence) >= 0.65
            else "low"
        )

    # rated_kwh and pack_mass_kg are observations in battery-data, not product
    # columns, so they arrive from the observation join when present. A pack
    # model without them cannot be built.
    for field, column in (("rated_kwh", "rated_kwh"), ("pack_mass_kg", "pack_mass_kg")):
        value = row.get(column)
        if value is not None:
            document[field] = float(value)

    if "rated_kwh" not in document or "pack_mass_kg" not in document:
        return None

    return document


# battery-data product uids for the components a pack model names.
_COMPONENT_SUFFIXES = ("bms", "hv_box", "thermal")


def _attach_components(
    documents: list[dict[str, Any] | None], components: list[dict[str, Any]]
) -> None:
    """Fold component market values into their pack documents, in place.

    Without this a pack rebuilt from the database would fall back to the
    archetype component template and lose the model-specific value of its BMS
    and HV box -- roughly a quarter of what parting one out is worth.
    """
    by_pack: dict[str, list[dict[str, Any]]] = {}
    for row in components:
        key = str(row["pack_uid"]).rsplit("/", 1)[-1]
        by_pack.setdefault(key, []).append(row)

    for document in documents:
        if document is None:
            continue
        rows = by_pack.get(document["key"])
        if not rows:
            continue
        overrides = {}
        for row in rows:
            uid = str(row["component_uid"])
            for suffix in _COMPONENT_SUFFIXES:
                if uid.endswith(f"-{suffix}"):
                    overrides[suffix] = {
                        "count": int(row["quantity"]),
                        "unit_value_eur": float(row["unit_value"]),
                    }
                    break
        if overrides:
            document["component_values"] = overrides


class BatteryDataPostgresProvider(PackDataProvider):
    """Reads pack models straight from a battery-data Postgres database."""

    key = "battery_data_pg"
    label = "battery-data (Postgres)"

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get(ENV_DSN)
        self._catalogue = None

    def is_available(self) -> bool:
        """Available when a DSN is configured and psycopg is installed."""
        if not self.dsn:
            return False
        try:
            import psycopg  # noqa: F401
        except ImportError:
            logger.info(
                "%s is set but psycopg is not installed; "
                "pip install 'battery-value[batterydata]'",
                ENV_DSN,
            )
            return False
        return True

    def reload(self) -> None:
        """Drop the cached catalogue so the next call re-queries."""
        self._catalogue = None

    def fetch_documents(self) -> list[dict[str, Any]]:
        """Every usable pack model in the database."""
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(_PACK_QUERY)
                rows = cursor.fetchall()
                cursor.execute(_COMPONENT_QUERY)
                components = [dict(row) for row in cursor.fetchall()]

        documents = [_row_to_document(dict(row)) for row in rows]
        _attach_components(documents, components)
        usable = [document for document in documents if document is not None]
        skipped = len(documents) - len(usable)
        if skipped:
            logger.info(
                "battery-data: %d of %d pack rows skipped (incomplete or weakly "
                "attributed)",
                skipped,
                len(documents),
            )
        return usable

    def _load(self):
        if self._catalogue is None:
            documents = self.fetch_documents()
            self._catalogue = (
                catalogue_from_documents(documents) if documents else None
            )
        return self._catalogue

    def find(self, passport: BatteryPassport) -> PackMatch | None:
        """Match against the database's pack models."""
        catalogue = self._load()
        return catalogue.match(passport) if catalogue else None


class BatteryDataHttpProvider(PackDataProvider):
    """Reads pack models from a battery-data HTTP API.

    Expects ``GET {base}/v1/packs`` to return battery-data's standard envelope,
    ``{"data": [...]}``, with one object per pack model.
    """

    key = "battery_data_http"
    label = "battery-data (HTTP)"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 12.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get(ENV_URL) or "").rstrip("/")
        self.timeout = timeout
        self.client = client
        self._catalogue = None

    def is_available(self) -> bool:
        """Available once a base URL is configured."""
        return bool(self.base_url)

    def reload(self) -> None:
        """Drop the cached catalogue so the next call re-fetches."""
        self._catalogue = None

    def fetch_documents(self) -> list[dict[str, Any]]:
        """Every usable pack model the service offers."""
        url = f"{self.base_url}/v1/packs"
        if self.client is not None:
            response = self.client.get(url, timeout=self.timeout)
        else:
            response = httpx.get(url, timeout=self.timeout, follow_redirects=True)
        response.raise_for_status()

        payload = response.json()
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        documents = [_row_to_document(dict(row)) for row in rows or ()]
        return [document for document in documents if document is not None]

    def _load(self):
        if self._catalogue is None:
            try:
                documents = self.fetch_documents()
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                logger.warning("battery-data HTTP fetch failed: %s", exc)
                return None
            self._catalogue = (
                catalogue_from_documents(documents) if documents else None
            )
        return self._catalogue

    def find(self, passport: BatteryPassport) -> PackMatch | None:
        """Match against the service's pack models."""
        catalogue = self._load()
        return catalogue.match(passport) if catalogue else None


def battery_data_providers() -> list[PackDataProvider]:
    """The configured battery-data layers, best first.

    Empty when neither is configured, which is the normal case for someone who
    has just cloned battery-value and wants it to work.
    """
    providers: list[PackDataProvider] = []
    postgres = BatteryDataPostgresProvider()
    if postgres.is_available():
        providers.append(postgres)
    http = BatteryDataHttpProvider()
    if http.is_available():
        providers.append(http)
    return providers


def refresh_snapshot(
    provider: PackDataProvider | None = None, *, path=None
) -> tuple[int, str]:
    """Regenerate the bundled pack catalogue from battery-data.

    This is what makes the bundled JSON a cache rather than a rival source of
    truth. Run it on a schedule; commit the result so a fresh clone still works
    with nothing configured.

    Args:
        provider: Where to read from. Defaults to the first configured layer.
        path: Destination file. Defaults to the bundled catalogue.

    Returns:
        ``(model_count, destination)``.

    Raises:
        RuntimeError: If no battery-data layer is configured, rather than
            silently writing an empty catalogue over a working one.
    """
    import json
    from importlib import resources

    if provider is None:
        candidates = battery_data_providers()
        if not candidates:
            raise RuntimeError(
                f"no battery-data source configured; set {ENV_DSN} or {ENV_URL}"
            )
        provider = candidates[0]

    documents = provider.fetch_documents()
    if not documents:
        raise RuntimeError(
            "battery-data returned no usable pack models; refusing to overwrite "
            "the bundled catalogue with an empty one"
        )

    from pathlib import Path

    bundled = resources.files("battery_value.packs.data").joinpath("pack_models.json")
    destination = Path(str(path)) if path is not None else Path(str(bundled))

    # Structure -- notes, the archetype mass split, component templates, labour
    # rates -- comes from the bundled file, and only the model list is
    # replaced. Reading the destination would fail the first time somebody
    # syncs somewhere new.
    existing = json.loads(bundled.read_text(encoding="utf-8"))
    existing["models"] = sorted(documents, key=lambda d: d["key"])
    existing["generated_from"] = f"battery-data via {provider.key}"
    destination.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return len(documents), str(destination)
