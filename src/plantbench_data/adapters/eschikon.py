"""Box-level source measurements and protected experimental assignments."""
import collections
import csv
import re
from datetime import datetime
from pathlib import Path


IMAGE = re.compile(r"extracted/images/images/(\d{8}|beet_roots)/(rgb|ir1|ir2|multispec)/(\d+)\.png")
# Transcribed from the acquired two-page README, Treatments at a Glance.
TREATMENTS = [
    ((1, 2, 3), "Control", 2, "Sufficient", "High", "None"),
    ((22, 23, 24), "Low N", 1, "Sufficient", "Low", "None"),
    ((4, 5, 6), "Med N", 2, "Sufficient", "Medium", "None"),
    ((7, 8, 9), "Med weeds", 2, "Sufficient", "High", "Medium"),
    ((10, 11, 12), "High weeds", 2, "Sufficient", "High", "High"),
    ((13, 14, 15), "Dry", 2, "Low", "Medium", "None"),
    ((16,), "Weed Only Dicot", 2, "Sufficient", "High", "High"),
    ((17,), "Weed Only Monocot", 2, "Sufficient", "High", "High"),
    ((18,), "Weed Only Mixed", 2, "Sufficient", "High", "High"),
    ((25, 26, 27), "Low N - Med Weed", 1, "Sufficient", "Low", "Medium"),
    ((19, 20, 21), "Drying - Med N - High Weed", 2, "Low", "Medium", "High"),
    ((28, 29, 30), "Drying - Low N", 1, "Low", "Low", "None"),
]


def decode_image(relative):
    match = IMAGE.fullmatch(relative)
    if not match:
        raise ValueError("Undocumented image path")
    stamp, modality, number = match.groups()
    box = int(number)
    if box not in range(1, 32):
        raise ValueError("Box number absent from source treatment table")
    return None if stamp == "beet_roots" else datetime.strptime(stamp, "%Y%m%d").date().isoformat(), modality, box


def source_date(value, fmt="%d.%m.%Y"):
    return {"collection_time": {"kind": "date", "value": datetime.strptime(value, fmt).date().isoformat(),
            "precision": "date", "source_value": value}}


def scalar(value):
    return None if value.strip() in {"", "NA", "N/A", "NaN", "nan"} else float(value)


def protected(ctx, key, subject, value, prov, kind, status="accepted", evidence="source_annotation"):
    env = ctx.envelope("annotation", key, [prov], processing_status=status)
    return ctx.add("annotations", {**env, "annotation_id": env["record_id"], "subject_ref": subject,
        "annotation_type": kind, "source_label": value, "mapping_status": "unmapped", "assertion_status": "not_assessed",
        "assertion_scope": "Source setting or unmapped document, not an observed plant diagnosis or action outcome",
        "verification_scope": "dataset_publication_level", "evidence_kind": evidence})


class EschikonAdapter:
    def convert(self, ctx):
        readme = ctx.provenance("downloads/README.pdf")
        greenhouse = ctx.subject("2018/greenhouse", "greenhouse", readme, cycle="source-2018-experiment", resolution="source_group")
        boxes = {n: ctx.subject(f"2018/box-{n}", "box", readme, cycle="source-2018-experiment", resolution="source_group",
                               source_fields={"box_number": n, "individual_plant_count": None}) for n in range(1, 31)}
        calibration = ctx.subject("2018/calibration-target-31", "image_only_case", readme,
                                  source_fields={"source_box_number": 31, "source_description": "CALTag and Color Checker", "biological_entity": False})
        auxiliary = ctx.subject("2018/source-auxiliary-files", "unknown", readme, resolution="unresolved")
        counts, exclusions = collections.Counter(), []
        for numbers, description, soil, water, nitrogen, weed in TREATMENTS:
            for number in numbers:
                protected(ctx, f"box-{number}/experimental-assignment", boxes[number],
                    {"description": description, "soil_type_code": soil, "water_input": water,
                     "nitrogen_input": nitrogen, "weed_pressure": weed, "source_page": 1,
                     "source_table": "Treatments at a Glance"}, readme, "experimental_group", evidence="experimental_assignment")
                ctx.link(f"greenhouse/contains/box-{number}", greenhouse, boxes[number], "source_greenhouse_contains_box",
                         [readme], "greenhouse", future_dependence=None,
                         source_fields={"measurement_scope": "greenhouse", "not_independent_per_box_sensor": True})
        for relative in sorted(ctx.files):
            if relative.startswith("extracted/images/") and relative.endswith(".png"):
                date, modality, number = decode_image(relative)
                prov = ctx.provenance(relative, "image")
                times = {"collection_time": {"kind": "date", "value": date, "precision": "date", "source_value": relative.split("/")[-3]}} if date else {}
                is_target = number == 31
                scope = "image_only_case" if is_target else "box"
                asset = ctx.asset(relative, prov, times, scope=scope,
                    modality="reference_figure" if is_target else {"rgb": "rgb", "ir1": "infrared_stereo_left", "ir2": "infrared_stereo_right", "multispec": "multispectral_source_mosaic"}[modality],
                    additional_provenance=[readme], localization_status="not_applicable" if is_target else "group_view",
                    source_fields={"source_camera": modality, "source_box_number": number, "source_scene": relative.split("/")[-3],
                                   "root_images_have_no_acquisition_date": date is None,
                                   "band_layout_and_radiometric_calibration": "not_applied; source mosaic is not a decoded spectral cube"})
                if is_target:
                    ctx.link(relative + "/calibration-target", asset, calibration, "source_calibration_target", [prov, readme], scope)
                    counts["calibration_target_images"] += 1
                else:
                    variable = "visual.rgb" if modality == "rgb" else "visual.nir" if modality.startswith("ir") else "visual.multispectral_source_mosaic"
                    ctx.observation(relative, boxes[number], variable, {"kind": "asset_ref", "value": asset}, modality,
                                    None, prov, "box", "visual_asset", times)
                    counts["box_images"] += 1
                counts["image/" + modality] += 1
            elif relative.lower().endswith((".png", ".jpg", ".jpeg")):
                prov = ctx.provenance(relative, "image")
                ctx.asset(relative, prov, scope="image_only_case", modality="reference_figure", localization_status="not_applicable")
                counts["auxiliary_images"] += 1
            elif relative.startswith("downloads/") and relative.endswith(".csv"):
                self.table(ctx, relative, boxes, greenhouse, calibration, counts)
            else:
                prov = ctx.provenance(relative)
                protected(ctx, relative + "/unmapped-source", auxiliary,
                          {"source_path": relative, "size_bytes": ctx.files[relative]["size_bytes"],
                           "reason": "Opaque point-cloud/calibration format or source documentation retained without invented measurement interpretation"},
                          prov, "unmapped_source_document", status="preserved_unmapped")
                exclusions.append({"source_path": relative, "processing_status": "preserved_unmapped"})
        converted = counts["box_images"] + counts["calibration_target_images"] + counts["auxiliary_images"] + counts["table_cells"] - counts["calibration_table_cells"]
        unmapped = len(exclusions) + counts["calibration_table_cells"]
        return {"source_record_definition": "one image member, one CSV scalar cell, or one non-image auxiliary file; treatment/context metadata are provenance-backed additions",
                "source_records_seen": converted + unmapped, "converted": converted, "preserved_unmapped": unmapped,
                "quarantined": 0, "rejected": 0, "counts": dict(counts), "exclusions": exclusions,
                "biological_box_subjects": 30, "calibration_target_subjects": 1,
                "limitations": ["No individual plant identity/localization", "Box total mass is not soil moisture",
                    "SPAD is not nitrogen concentration", "Harvest measurement date absent from yield CSV",
                    "Raw multispectral mosaics retained; calibrated bands and point clouds not decoded",
                    "Noncommercial internal research scope only"]}

    def table(self, ctx, relative, boxes, greenhouse, calibration, counts):
        with (ctx.source_root / relative).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            headers = next(reader)
            for values in reader:
                line = reader.line_num  # These source CSVs have one physical line per row.
                if len(values) != len(headers):
                    raise ValueError("CSV field count differs from locked schema")
                name = Path(relative).name
                if name == "greenhouse-temperature-humidity-logs.csv":
                    if headers != ["Date", "Time", "Room Temperature (degree Celsius)", "Relative Humidity (%)"]:
                        raise ValueError("Greenhouse CSV headers changed")
                    stamp = datetime.strptime(values[0] + " " + values[1], "%d.%m.%y %H:%M:%S").isoformat()
                    times = {"collection_time": {"kind": "datetime", "value": stamp, "precision": "second", "source_value": " ".join(values[:2])}}
                    columns = [(2, "environment.air_temperature", "degC"), (3, "environment.relative_humidity", "%")]
                    subject, scope, evidence = greenhouse, "greenhouse", "sensor_measurement"
                else:
                    if headers[0] != "Box Number" or int(values[0]) not in range(1, 32):
                        raise ValueError("Unresolved box identity in CSV")
                    subject, scope, evidence = boxes.get(int(values[0]), calibration), "box", "manual_measurement"
                    if name == "box-weights-kg.csv":
                        columns = [(i, "container.total_mass", "kg") for i in range(1, len(headers))]
                    elif name == "spad-measurements.csv":
                        columns = [(i, "phenotype.spad_index", "SPAD_index") for i in range(1, len(headers))]
                    elif name == "yield-weight-grams.csv":
                        variables = {"Fresh leaves weight": "phenotype.fresh_leaf_mass", "Dry leaves weight": "phenotype.dry_leaf_mass",
                                     "Fresh beet weight": "phenotype.fresh_beet_mass", "Dry beet weight": "phenotype.dry_beet_mass"}
                        if set(headers[1:]) != set(variables):
                            raise ValueError("Yield CSV headers changed")
                        columns = [(i, variables[h], "g") for i, h in enumerate(headers) if i]
                    else:
                        raise ValueError("Unexpected root CSV")
                for column, variable, unit in columns:
                    if name not in {"greenhouse-temperature-humidity-logs.csv", "yield-weight-grams.csv"}:
                        times = source_date(headers[column])
                    elif name == "yield-weight-grams.csv":
                        times = {}
                    prov = ctx.provenance(relative, "csv", row=line, column=headers[column])
                    counts["table_cells"] += 1
                    counts["cells/" + name] += 1
                    if subject == calibration:
                        protected(ctx, f"{relative}/line-{line}/{column}/calibration-placeholder", subject,
                                  {"source_column": headers[column], "value": values[column]}, prov,
                                  "calibration_target_table_placeholder", status="preserved_unmapped")
                        counts["calibration_table_cells"] += 1
                        continue
                    value = scalar(values[column])
                    ctx.observation(f"{relative}/line-{line}/{column}", subject, variable, {"kind": "scalar", "value": value}, values[column],
                                    unit, prov, scope, evidence, times,
                                    source_fields={"source_column": headers[column], "measurement_date_missing": not bool(times)})
