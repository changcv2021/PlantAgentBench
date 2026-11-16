"""Preserve the released image series and documented experimental identity."""
import re
import collections
from pathlib import PurePosixPath

NAME = re.compile(r"(?P<group>H|V)_(?P<day>\d+)_(?P<camera>RGB|VNIR)(?:_(?P<band>800nm|1000nm|))?_(?P<angle>\d+)_(?P<pot>\d+)\.(?:tif|tiff|png)", re.I)


def decode_name(relative):
    path = PurePosixPath(relative)
    if len(path.parts) != 6 or path.parts[0] != "TOBRFV-LMID":
        raise ValueError("Unexpected released path layout")
    _, modality, species, cultivar, condition, filename = path.parts
    match = NAME.fullmatch(filename)
    if not match:
        raise ValueError("Filename does not match documented naming rule")
    fields = match.groupdict()
    expected_modality = fields["camera"].upper() + ("_" + fields["band"] if fields["band"] else "")
    if modality != expected_modality:
        raise ValueError("Folder and filename disagree on modality")
    expected_condition = "Healthy" if fields["group"].upper() == "H" else "Virus"
    if condition != expected_condition or species not in {"Tomato", "Pepper"} or cultivar not in {"I", "II"}:
        raise ValueError("Folder identity requires source review")
    return {"species_original": species, "cultivar_code": cultivar, "source_group": condition,
            "pot_token": fields["pot"], "subject_key": f"2023/{species}/{cultivar}/{condition}/pot-{fields['pot']}",
            "experiment_day": int(fields["day"]), "angle_degrees": int(fields["angle"]), "modality": modality,
            "absolute_date": None, "laboratory_assay_status": "not_recorded_in_filename",
            "condition_evidence_kind": "experimental_assignment"}


def multiview_subject_variables(angles):
    """Views must share source day AND exact acquisition modality/band."""
    return {(subject, "visual.rgb" if modality == "RGB" else "visual.nir")
            for (subject, modality, day), values in angles.items() if len(values) >= 2}


class ToBRFVAdapter:
    def convert(self, ctx):
        paper = ctx.external_provenance(ctx.mapping["paper_evidence_ref"])
        subjects, days, angles = {}, collections.defaultdict(set), collections.defaultdict(set)
        histogram, exclusions = collections.Counter(), []
        for relative in sorted(ctx.files):
            if PurePosixPath(relative).suffix.lower() not in {".png", ".tif", ".tiff"}:
                exclusions.append({"path": relative, "status": "preserved_unmapped", "reason": "non_image_source_member"})
                continue
            # A malformed identity fails the run rather than silently changing the
            # planned complete-release scope. Investigate before a revised build.
            info = decode_name(relative)
            prov = ctx.provenance(relative, "image")
            key = info["subject_key"]
            if key not in subjects:
                subject = ctx.subject(key, "pot", prov, cycle="released-2023-experiment",
                    source_fields={"species_original": info["species_original"], "cultivar_source_code": info["cultivar_code"],
                                   "identity_namespace": "species/cultivar/experimental_group/pot_token"})
                subjects[key] = subject
                ann_key = key + "/source-experimental-group"
                env = ctx.envelope("annotation", ann_key, [prov, paper])
                annotation = ctx.add("annotations", {**env, "annotation_id": env["record_id"], "subject_ref": subject,
                    "annotation_type": "experimental_group", "source_label": info["source_group"],
                    "canonical_concept_id": None, "mapping_status": "unmapped", "assertion_status": "positive",
                    "assertion_scope": "Membership of the released experimental group, not a per-timepoint infection or assay result",
                    "verification_method": "Release filename/folder and publication; per-pot per-day laboratory records are not in this archive",
                    "verification_scope": "dataset_publication_level", "evidence_kind": "experimental_assignment"})
                ctx.capabilities[(subject, "annotation.experimental_assignment", "pot", "experimental_assignment", "not_applicable", False)].append((annotation, prov))
            subject = subjects[key]
            modality = info["modality"]
            if modality == "RGB":
                canonical_modality, variable, wavelength = "rgb", "visual.rgb", None
            elif modality == "VNIR":
                canonical_modality, variable = "vnir_broadband", "visual.nir"
                wavelength = {"band_kind": "broadband", "minimum_nm": 400, "maximum_nm": 1100,
                              "source_description": "Published camera range; integrated broadband capture, not a hyperspectral cube"}
            else:
                center = 800 if modality == "VNIR_800nm" else 1000
                canonical_modality, variable = f"nir_{center}nm", "visual.nir"
                wavelength = {"band_kind": "narrowband", "center_nm": center,
                              "source_description": "Published nominal bandpass-filter wavelength; FWHM not specified"}
            times = {"collection_time": {"kind": "experiment_day", "value": info["experiment_day"], "precision": "index",
                      "timezone_status": "not_applicable", "source_value": PurePosixPath(relative).name}}
            asset = ctx.asset(relative, prov, times, scope="pot", modality=canonical_modality,
                wavelength_metadata=wavelength, additional_provenance=[paper], localization_status="source_localized",
                source_fields={"view_angle_degrees": info["angle_degrees"], "release_modality": modality,
                    "source_processing": "Publisher-cropped images; RGB converted from camera RAW to TIFF. Original camera RAW not included.",
                    "absolute_date_origin": "not_assigned"})
            ctx.observation(relative, subject, variable, {"kind": "asset_ref", "value": asset}, relative, None, prov, "pot", "visual_asset", times)
            days[(subject, variable)].add(info["experiment_day"])
            angles[(subject, modality, info["experiment_day"])].add(info["angle_degrees"])
            histogram[modality] += 1
        # History requires repeated real source day indices for the same pot.
        multiview = multiview_subject_variables(angles)
        for key, refs in list(ctx.capabilities.items()):
            subject, variable, scope, evidence, unit, flagged = key
            if variable == "visual.rgb" and len(days[(subject, variable)]) >= 2:
                ctx.capabilities[(subject, "visual.rgb_history", scope, evidence, unit, flagged)] = list(refs)
            if (subject, variable) in multiview:
                ctx.capabilities[(subject, "visual.multiview", scope, evidence, unit, flagged)].extend(refs)
        return {"source_records_seen": sum(histogram.values()) + len(exclusions), "converted": sum(histogram.values()),
                "preserved_unmapped": len(exclusions), "quarantined": 0, "rejected": 0,
                "modality_counts": dict(histogram), "source_pot_identities": len(subjects), "exclusions": exclusions,
                "laboratory_assay_rows": 0, "assay_reason": "No row-level laboratory files in complete released archive",
                "publication_image_count": 40716, "initial_zenodo_description_image_count": 42736,
                "date_origin": "Source experiment-day indices retained; no invented absolute-date origin or timezones"}
