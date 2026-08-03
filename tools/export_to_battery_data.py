#!/usr/bin/env python3
"""Emit the battery-value reference data as battery-data seed SQL.

battery-data is the source of truth for what a battery *is*: which pack is in
which vehicle, what it is assembled from, what it is made of. battery-value
currently carries that in bundled JSON, which makes it a second source of
truth for facts somebody else models better.

This script converts one into the other, so the migration is reproducible and
reviewable rather than a one-off hand edit. Run it, load the SQL, and the pack
catalogue lives where the provenance discipline is.

    python tools/export_to_battery_data.py > ../battery-data/seed/002_packs_and_valuation.sql

What it does NOT emit is metal prices. Those are a licensed daily series and
belong in the consumer's own feed; see docs/market-data.md.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "battery_value"
sys.path.insert(0, str(SRC.parent))

from battery_value.compounds import TRADED_FORMS  # noqa: E402
from battery_value.packs.catalogue import load_catalogue  # noqa: E402

DATA = SRC / "materials" / "data"
VALID_FROM = "2026-01-01"

# Manufacturers referenced by the catalogue, with the country that fields
# them. battery-data keys organisations by uid, so these must be stable.
ORGANISATIONS = {
    "Nissan": ("org/nissan", "Nissan Motor", "JP"),
    "Renault": ("org/renault", "Renault Group", "FR"),
    "BMW": ("org/bmw", "BMW Group", "DE"),
    "Tesla": ("org/tesla", "Tesla", "US"),
    "Volkswagen": ("org/volkswagen", "Volkswagen Group", "DE"),
    "Hyundai": ("org/hyundai", "Hyundai Motor Group", "KR"),
    "BYD": ("org/byd", "BYD Auto", "CN"),
    "Stellantis": ("org/stellantis", "Stellantis", "NL"),
    "Audi": ("org/audi", "Audi", "DE"),
    "Polestar": ("org/polestar", "Polestar", "SE"),
    "Toyota": ("org/toyota", "Toyota Motor", "JP"),
}

# Every claim needs a source. These are the four this export rests on, kept
# honest about what each one can actually support.
SOURCES = [
    (
        "src/bv-pack-catalogue",
        "teardown_report",
        "battery-value pack catalogue (teardowns and service documentation)",
        "https://github.com/Morshedvarzandeh/battery-worldcup",
        "Compiled from OEM service documentation, homologation filings and "
        "published pack teardowns. Individual figures vary in strength; the "
        "confidence column carries that.",
    ),
    (
        "src/eu-2023-1542-annex-xii",
        "standard",
        "Regulation (EU) 2023/1542 Annex XII, recycling efficiency and "
        "material recovery targets",
        "https://eur-lex.europa.eu/eli/reg/2023/1542/oj",
        "Regulatory minima with fixed compliance dates. These are floors on "
        "recovery, and say nothing about what a refiner pays.",
    ),
    (
        "src/bv-recycling-terms",
        "third_party_test",
        "battery-value recycling process and payable terms",
        "https://github.com/Morshedvarzandeh/battery-worldcup",
        "Commercial recovery rates and black-mass payable terms, benchmarked "
        "from published plant mass balances and reported offtake structures. "
        "Payables in particular are negotiated and move with the market.",
    ),
    (
        "src/bv-used-parts-market",
        "distributor_listing",
        "battery-value used-parts market observations",
        "https://github.com/Morshedvarzandeh/battery-worldcup",
        "Used module and component values from second-hand marketplace "
        "listings. A thin market, so treat as indicative and refresh often.",
    ),
]

# Which components of a pack are modelled as products in their own right.
# An enclosure is not a product anyone sells; a module and a BMS are.
COMPONENT_KINDS = {
    "modules": ("module", "Battery module"),
    "bms": ("component", "Battery management system"),
    "hv_box": ("component", "HV junction box"),
    "thermal": ("component", "Cooling plate assembly"),
}

# battery-data's form_factor enum, which is narrower than free text.
FORM_FACTORS = {
    "pouch": "pouch",
    "prismatic": "prismatic_hardcase",
    "prismatic nimh": "prismatic_hardcase",
    "blade prismatic": "blade",
    "cylindrical 2170": "cylindrical",
    "cylindrical 18650": "cylindrical",
}


def q(value) -> str:
    """Quote a value for SQL, mapping None to NULL."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def array(values) -> str:
    """A Postgres text[] literal."""
    if not values:
        return "'{}'"
    inner = ",".join('"' + str(v).replace('"', '\\"') + '"' for v in values)
    return q("{" + inner + "}")


def org_ref(uid: str) -> str:
    return f"(SELECT id FROM organization WHERE uid={q(uid)})"


def prov_ref(name: str) -> str:
    return f"(SELECT id FROM provenance WHERE derivation_note={q(name)})"


def rev_ref(uid: str) -> str:
    return f"(SELECT id FROM product_revision WHERE uid={q(uid)})"


def slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def emit(line: str = "") -> None:
    print(line)


def header() -> None:
    emit("-- " + "=" * 69)
    emit("-- battery-data : seed/002_packs_and_valuation.sql")
    emit("--")
    emit("-- Pack models, the vehicles they are fielded in, and the end-of-life")
    emit("-- economics needed to value one.")
    emit("--")
    emit("-- GENERATED. Do not hand-edit; regenerate with")
    emit("--   python tools/export_to_battery_data.py   (in battery-worldcup)")
    emit("--")
    emit("-- Attribution is deliberately conservative. Pack-to-vehicle links here")
    emit("-- are service documentation and teardowns, not manufacturer statements,")
    emit("-- so they carry basis='teardown' and a confidence below 1. Where a")
    emit("-- catalogue entry was itself marked medium confidence, that is carried")
    emit("-- through rather than rounded up.")
    emit("-- " + "=" * 69)
    emit()
    emit("SET search_path = bd, public;")
    emit()


def emit_organisations() -> None:
    emit("-- " + "-" * 69)
    emit("-- Organisations")
    emit("-- " + "-" * 69)
    rows = ",\n  ".join(
        f"({q(uid)},{q(name)},{q(country)},'{{manufacturer}}')"
        for uid, name, country in sorted(set(ORGANISATIONS.values()))
    )
    emit("INSERT INTO organization (uid, name, country, roles) VALUES")
    emit(f"  {rows}")
    emit("ON CONFLICT (uid) DO NOTHING;")
    emit()


def emit_sources_and_provenance() -> None:
    emit("-- " + "-" * 69)
    emit("-- Contributor, sources and provenance")
    emit("--")
    emit("-- One provenance row per source, because the evidence class is a")
    emit("-- property of the source rather than of each value. derivation_note")
    emit("-- carries the export's own key so the rows below can reference it.")
    emit("-- " + "-" * 69)
    emit(
        "INSERT INTO contributor (uid, display_name, is_bot) VALUES\n"
        "  ('user/battery-value-export','battery-value export', true)\n"
        "ON CONFLICT (uid) DO NOTHING;"
    )
    emit()

    for uid, kind, title, url, note in SOURCES:
        emit(
            "INSERT INTO source (uid, kind, title, url, license, redistributable,\n"
            "                    retrieved_at, scope_note)\n"
            f"VALUES ({q(uid)},{q(kind)},{q(title)},{q(url)},'MIT', true, now(),\n"
            f"        {q(note)})\n"
            "ON CONFLICT (uid) DO NOTHING;"
        )
        emit(
            "INSERT INTO source_location (source_id, locator_kind)\n"
            f"SELECT id,'dataset' FROM source WHERE uid={q(uid)}\n"
            "ON CONFLICT DO NOTHING;"
        )

    emit()
    evidence = {
        "src/bv-pack-catalogue": ("manufacturer_claim", 0.80),
        "src/eu-2023-1542-annex-xii": ("literature_reported", 1.00),
        "src/bv-recycling-terms": ("estimated", 0.70),
        "src/bv-used-parts-market": ("estimated", 0.60),
    }
    for uid, (evidence_class, confidence) in evidence.items():
        emit(
            "INSERT INTO provenance (source_location_id, evidence, extraction,\n"
            "                        contributor_id, confidence, review,\n"
            "                        derivation_note)\n"
            "SELECT sl.id, "
            f"{q(evidence_class)}::evidence_class,'manual_entry'::extraction_method,\n"
            "       (SELECT id FROM contributor WHERE uid='user/battery-value-export'),\n"
            f"       {confidence}, 'pending_review'::review_state, {q(uid)}\n"
            f"  FROM source_location sl JOIN source s ON s.id=sl.source_id\n"
            f" WHERE s.uid={q(uid)}\n"
            " LIMIT 1;"
        )
    emit()


def emit_traded_forms() -> None:
    emit("-- " + "-" * 69)
    emit("-- Traded forms")
    emit("--")
    emit("-- contained_fraction is computed from the formula and IUPAC atomic")
    emit("-- weights, not typed in. Lithium carbonate is 18.785% lithium, which")
    emit("-- is where the industry's LCE factor of 5.323 comes from.")
    emit("-- " + "-" * 69)
    emit(
        "INSERT INTO traded_form (uid, code, label, formula, payable_element,\n"
        "                         contained_fraction, notes) VALUES"
    )
    rows = []
    for key, form in sorted(TRADED_FORMS.items()):
        rows.append(
            f"  ({q('form/' + key.replace('_', '-'))},{q(key)},{q(form.label)},"
            f"{q(form.formula)},{q(form.payable_element)},"
            f"{form.contained_fraction():.7f},{q(form.note or None)})"
        )
    emit(",\n".join(rows))
    emit("ON CONFLICT (uid) DO NOTHING;")
    emit()


def emit_recovery(recovery: dict) -> None:
    emit("-- " + "-" * 69)
    emit("-- Recovery processes, yields and costs")
    emit("-- " + "-" * 69)

    route_of = {
        "hydrometallurgical": "hydrometallurgical",
        "pyrometallurgical": "pyrometallurgical",
        "direct_recycling": "direct_recycling",
        "nimh_stainless_smelting": "stainless_smelting",
        "lead_acid_smelting": "lead_smelting",
    }

    for key, process in recovery["processes"].items():
        uid = f"process/{key.replace('_', '-')}"
        emit(
            "INSERT INTO recovery_process (uid, route, name, description,\n"
            "                              maturity, applies_to) VALUES\n"
            f"  ({q(uid)},{q(route_of[key])},{q(process['label'])},"
            f"{q(process.get('description'))},\n"
            f"   {q(process.get('maturity', 'commercial'))},"
            f"{array(process['applies_to_families'])})\n"
            "ON CONFLICT (uid) DO NOTHING;"
        )

        rows = []
        for element, terms in process["elements"].items():
            if terms["recovery_rate"] <= 0 and terms["payable_fraction"] <= 0:
                continue
            rows.append(
                f"  ((SELECT id FROM recovery_process WHERE uid={q(uid)}),"
                f"{q(element)},\n"
                f"   (SELECT id FROM traded_form WHERE code={q(terms['traded_form'])}),"
                f"{terms['recovery_rate']},{terms['payable_fraction']},"
                f"{q(VALID_FROM)},'EU',\n"
                f"   {prov_ref('src/bv-recycling-terms')})"
            )
        if rows:
            emit(
                "INSERT INTO recovery_yield (recovery_process_id, element_symbol,\n"
                "       traded_form_id, recovery_rate, payable_fraction,\n"
                "       valid_from, region, provenance_id) VALUES"
            )
            emit(",\n".join(rows) + ";")

        cost_rows = []
        for stage, rate in process["cost_eur_per_kg"].items():
            if rate <= 0:
                continue
            cost_rows.append(
                f"  ((SELECT id FROM recovery_process WHERE uid={q(uid)}),"
                f"{q(stage)},{rate},'EUR',{q(VALID_FROM)},'EU',\n"
                f"   {prov_ref('src/bv-recycling-terms')})"
            )
        if cost_rows:
            emit(
                "INSERT INTO treatment_cost (recovery_process_id, stage,\n"
                "       cost_per_kg, currency, valid_from, region, provenance_id) VALUES"
            )
            emit(",\n".join(cost_rows) + ";")
        emit()

    emit("-- Regulatory floors, kept as separate rows from commercial practice.")
    emit("-- EU 2023/1542 Annex XII, from 31 December 2027 and 2031.")
    regulatory = [
        ("2027-12-31", {"Co": 0.90, "Cu": 0.90, "Pb": 0.90, "Ni": 0.90, "Li": 0.50}),
        ("2031-12-31", {"Co": 0.95, "Cu": 0.95, "Pb": 0.95, "Ni": 0.95, "Li": 0.80}),
    ]
    rows = []
    for valid_from, targets in regulatory:
        for element, rate in targets.items():
            rows.append(
                "  ((SELECT id FROM recovery_process WHERE "
                f"uid='process/hydrometallurgical'),{q(element)},{rate},NULL,\n"
                f"   {q(valid_from)},'EU', true,"
                f"{prov_ref('src/eu-2023-1542-annex-xii')})"
            )
    emit(
        "INSERT INTO recovery_yield (recovery_process_id, element_symbol,\n"
        "       recovery_rate, payable_fraction, valid_from, region,\n"
        "       is_regulatory_minimum, provenance_id) VALUES"
    )
    emit(",\n".join(rows) + ";")
    emit()

    logistics = recovery["logistics"]
    emit("-- Dangerous-goods freight. UN3480/3481 Class 9; damaged and defective")
    emit("-- packs fall under ADR special provision 376, hence the multipliers.")
    rows = []
    for condition, multiplier in logistics["condition_multiplier"].items():
        rows.append(
            f"  ({q(condition)},'UN3481',"
            f"{round(logistics['base_eur_per_kg'] * multiplier, 4)},"
            f"{logistics['minimum_charge_eur']},'EUR','road',{q(VALID_FROM)},'EU',\n"
            f"   {prov_ref('src/bv-recycling-terms')})"
        )
    emit(
        "INSERT INTO logistics_tariff (condition, un_number, cost_per_kg,\n"
        "       minimum_charge, currency, mode, valid_from, region,\n"
        "       provenance_id) VALUES"
    )
    emit(",\n".join(rows) + ";")
    emit()


def emit_assumptions(recovery: dict) -> None:
    emit("-- " + "-" * 69)
    emit("-- Model calibration")
    emit("-- " + "-" * 69)

    second_life = recovery["second_life"]
    reuse = recovery["reuse"]
    entries = [
        ("second_life.testing_eur_per_kwh", "second_life",
         second_life["testing_eur_per_kwh"], "EUR/kWh"),
        ("second_life.repackaging_eur_per_kwh", "second_life",
         second_life["repackaging_eur_per_kwh"], "EUR/kWh"),
        ("second_life.new_bms_eur_per_pack", "second_life",
         second_life["new_bms_eur_per_pack"], "EUR"),
        ("second_life.certification_eur_per_pack", "second_life",
         second_life["certification_eur_per_pack"], "EUR"),
        ("second_life.warranty_reserve_fraction", "second_life",
         second_life["warranty_reserve_fraction"], "fraction"),
        ("second_life.minimum_viable_soh", "second_life",
         second_life["minimum_viable_soh"], "fraction"),
        ("second_life.end_of_life_soh", "second_life",
         second_life["second_life_end_of_life_soh"], "fraction"),
        ("reuse.minimum_viable_soh", "reuse", reuse["minimum_viable_soh"], "fraction"),
        ("reuse.maximum_age_years", "reuse", reuse["maximum_age_years"], "years"),
        ("reuse.refurbishment_eur_per_kwh", "reuse",
         reuse["refurbishment_eur_per_kwh"], "EUR/kWh"),
        ("reuse.test_and_certify_eur_per_pack", "reuse",
         reuse["test_and_certify_eur_per_pack"], "EUR"),
        ("reuse.oem_replacement_price_discount", "reuse",
         reuse["oem_replacement_price_discount"], "fraction"),
        ("reuse.warranty_reserve_fraction", "reuse",
         reuse["warranty_reserve_fraction"], "fraction"),
    ]

    catalogue = load_catalogue()
    entries.append(
        ("parts_out.labour_rate_eur_per_hour", "parts_out",
         catalogue.labour_rate_eur_per_hour, "EUR/h")
    )
    entries.append(
        ("parts_out.fixed_setup_minutes", "parts_out",
         catalogue.fixed_setup_minutes, "minutes")
    )

    rows = [
        f"  ({q(key)},{q(pathway)},{value},{q(unit)},{q(VALID_FROM)},'EU',\n"
        f"   {prov_ref('src/bv-recycling-terms')})"
        for key, pathway, value, unit in entries
    ]
    emit(
        "INSERT INTO valuation_assumption (key, pathway, value_num, unit,\n"
        "       valid_from, region, provenance_id) VALUES"
    )
    emit(",\n".join(rows) + ";")
    emit()


def emit_packs() -> None:
    catalogue = load_catalogue()
    emit("-- " + "-" * 69)
    emit(f"-- Pack products ({len(catalogue.models)}), their assemblies,")
    emit("-- the vehicles they are fielded in, and what the parts sell for.")
    emit("-- " + "-" * 69)

    for model in catalogue.models:
        org_uid = ORGANISATIONS.get(model.manufacturer, (None,))[0]
        if org_uid is None:
            print(
                f"-- SKIPPED {model.key}: no organisation mapped for "
                f"{model.manufacturer}",
                file=sys.stderr,
            )
            continue

        pack_uid = f"pack/{slug(model.manufacturer)}/{model.key}"
        rev_uid = f"{pack_uid}@bv"
        emit(f"-- {model.label}")
        emit(
            "INSERT INTO product (uid, kind, manufacturer_id, model_number, brand,\n"
            "                     form_factor, lifecycle, is_rechargeable, notes)\n"
            f"VALUES ({q(pack_uid)},'pack',{org_ref(org_uid)},{q(model.key)},"
            f"{q(model.manufacturer)},\n"
            f"        {q(FORM_FACTORS.get((model.cell_format or '').lower()))},"
            "'unknown', true,\n"
            f"        {q(model.notes or None)})\n"
            "ON CONFLICT (uid) DO NOTHING;"
        )
        emit(
            "INSERT INTO product_revision (uid, product_id, source_id,\n"
            "                              revision_label, region_scope)\n"
            f"SELECT {q(rev_uid)}, p.id, s.id, 'bv-catalogue', '{{EU}}'\n"
            f"  FROM product p, source s\n"
            f" WHERE p.uid={q(pack_uid)} AND s.uid='src/bv-pack-catalogue'\n"
            "ON CONFLICT (uid) DO NOTHING;"
        )
        emit(
            "INSERT INTO product_chemistry (product_revision_id, designation,\n"
            "                               provenance_id)\n"
            f"SELECT {rev_ref(rev_uid)},{q(model.chemistry)},"
            f"{prov_ref('src/bv-pack-catalogue')}\n"
            "ON CONFLICT (product_revision_id) DO NOTHING;"
        )

        # Nameplate energy and pack mass are observations in battery-data,
        # not product columns, because both are measured under a convention
        # the source may or may not state. value_si keeps them comparable;
        # value_native keeps them auditable.
        #
        # The catalogue's energy figures are OEM nameplate ratings, and no
        # source in it states the rate or temperature they were measured at.
        # condition_set.unstated records that omission as a fact about the
        # documents rather than inventing a plausible 25 C and 0.2C, which
        # would manufacture precision nobody published.
        emit(
            "INSERT INTO observation (product_revision_id, quantity_id, statistic,\n"
            "       value_native, unit_native, value_si, condition_set_id,\n"
            "       provenance_id)\n"
            f"SELECT {rev_ref(rev_uid)}, q.id, 'nominal'::statistic_kind,\n"
            f"       {model.rated_kwh},'kWh',{model.rated_kwh * 3.6e6},\n"
            "       bd.intern_conditions('{\"unstated\":"
            "[\"rate_value\",\"rate_unit\",\"temperature_c\"]}'::jsonb),\n"
            f"       {prov_ref('src/bv-pack-catalogue')}\n"
            "  FROM quantity q WHERE q.code='energy';"
        )
        emit(
            "INSERT INTO observation (product_revision_id, quantity_id, statistic,\n"
            "       value_native, unit_native, value_si, provenance_id)\n"
            f"SELECT {rev_ref(rev_uid)}, q.id, 'nominal'::statistic_kind,\n"
            f"       {model.pack_mass_kg},'kg',{model.pack_mass_kg},\n"
            f"       {prov_ref('src/bv-pack-catalogue')}\n"
            "  FROM quantity q WHERE q.code='mass';"
        )

        # Modules are a product; the enclosure is not. Only emit the ones
        # somebody could actually buy.
        module = model.component("modules")
        if module is not None and model.module_count:
            module_uid = f"module/{slug(model.manufacturer)}/{model.key}"
            module_rev = f"{module_uid}@bv"
            emit(
                "INSERT INTO product (uid, kind, manufacturer_id, model_number,\n"
                "                     brand, lifecycle, is_rechargeable)\n"
                f"VALUES ({q(module_uid)},'module',{org_ref(org_uid)},"
                f"{q(model.key + '-module')},\n"
                f"        {q(model.manufacturer)},'unknown', true)\n"
                "ON CONFLICT (uid) DO NOTHING;"
            )
            emit(
                "INSERT INTO product_revision (uid, product_id, source_id,\n"
                "                              revision_label)\n"
                f"SELECT {q(module_rev)}, p.id, s.id, 'bv-catalogue'\n"
                f"  FROM product p, source s\n"
                f" WHERE p.uid={q(module_uid)} AND s.uid='src/bv-pack-catalogue'\n"
                "ON CONFLICT (uid) DO NOTHING;"
            )
            emit(
                "INSERT INTO product_assembly (parent_revision_id, child_revision_id,\n"
                "       quantity, provenance_id)\n"
                f"SELECT {rev_ref(rev_uid)},{rev_ref(module_rev)},"
                f"{model.module_count},\n"
                f"       {prov_ref('src/bv-pack-catalogue')}\n"
                "ON CONFLICT (parent_revision_id, child_revision_id) DO NOTHING;"
            )
            if module.unit_value_eur > 0:
                emit(
                    "INSERT INTO component_market_value (product_revision_id,\n"
                    "       unit_value, currency, assumed_soh, sell_through,\n"
                    "       valid_from, region, provenance_id)\n"
                    f"SELECT {rev_ref(module_rev)},{module.unit_value_eur},'EUR',"
                    "1.0,0.85,\n"
                    f"       {q(VALID_FROM)},'EU',"
                    f"{prov_ref('src/bv-used-parts-market')};"
                )

        if model.oem_replacement_price_eur_per_kwh:
            emit(
                "INSERT INTO replacement_price (product_revision_id, price_per_kwh,\n"
                "       currency, includes_labour, valid_from, region, provenance_id)\n"
                f"SELECT {rev_ref(rev_uid)},"
                f"{model.oem_replacement_price_eur_per_kwh},'EUR', false,\n"
                f"       {q(VALID_FROM)},'EU',{prov_ref('src/bv-used-parts-market')};"
            )

        for vehicle in model.vehicle_models:
            app_uid = f"app/{slug(vehicle)}"
            emit(
                "INSERT INTO application (uid, name, sector, operator_text, region,\n"
                "                         in_service_from)\n"
                f"VALUES ({q(app_uid)},{q(vehicle)},'passenger_vehicle',"
                f"{q(model.manufacturer)},'EU',\n"
                f"        {q(f'{model.years[0]}-01-01' if model.years else None)})\n"
                "ON CONFLICT (uid) DO NOTHING;"
            )
            confidence = {"high": 0.85, "medium": 0.65, "low": 0.45}.get(
                model.confidence, 0.65
            )
            emit(
                "INSERT INTO product_application (application_id,\n"
                "       product_revision_id, role, quantity_per_unit, basis,\n"
                "       confidence, provenance_id)\n"
                f"SELECT a.id,{rev_ref(rev_uid)},'traction',1,'teardown',"
                f"{confidence},\n"
                f"       {prov_ref('src/bv-pack-catalogue')}\n"
                f"  FROM application a WHERE a.uid={q(app_uid)}\n"
                "ON CONFLICT DO NOTHING;"
            )
        emit()


def main() -> int:
    recovery = json.loads((DATA / "recovery.json").read_text(encoding="utf-8"))

    header()
    emit_organisations()
    emit_sources_and_provenance()
    emit_traded_forms()
    emit_recovery(recovery)
    emit_assumptions(recovery)
    emit_packs()

    emit("-- " + "-" * 69)
    emit(f"-- Generated {date.today().isoformat()} from battery-value data files.")
    emit("-- " + "-" * 69)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
